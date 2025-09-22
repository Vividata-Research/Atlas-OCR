// cdk/helpers/dotsOcrBatch.ts
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";

// Version for tracking deployments - increment when making breaking changes
export const DEPLOYMENT_VERSION = "v1";

// Default identifiers (used when the state machine is executed without explicit input)
export const DEFAULT_PARAMS = {
  instance_type: "ml.g5.xlarge",     // default GPU
  input_prefix: "incoming/",          // s3://<input-bucket>/incoming/...
  output_prefix: "processed/runs/1/", // s3://<output-bucket>/processed/runs/1/...
} as const;

export interface RetryLogicResult {
  initRetryCounter: sfn.Pass;
  prepareBatchInput: sfn.Pass;
  batchTask: tasks.SageMakerCreateTransformJob;
}

/** Helper to build transform-job names (unique, concise) */
export const buildTransformJobName = (truncatedBase: string, suffix = "bt") =>
  sfn.JsonPath.format(
    `${truncatedBase}-${suffix}-${DEPLOYMENT_VERSION}-{}-{}`,
    sfn.JsonPath.stringAt("$$.Execution.Name"),
    sfn.JsonPath.stringAt("$.retryCount"),
  );

/** Same retry loop you use elsewhere (capacity/throttling) */
export const createRetryLogic = (
  scope: Construct,
  idPrefix: string,
  batchTask: tasks.SageMakerCreateTransformJob
): RetryLogicResult => {
  // NOTE: mirror your working code: copy the WHOLE state to $.input here
  const initRetryCounter = new sfn.Pass(scope, `${idPrefix}InitRetryCounter`, {
    parameters: {
      retryCount: 0,
      "input.$": "$",
    },
    resultPath: "$",
  });

  const incrementRetry = new sfn.Pass(scope, `${idPrefix}IncrementRetry`, {
    parameters: {
      "retryCount.$": "States.MathAdd($.retryCount, 1)",
      "input.$": "$.input",
    },
    resultPath: "$",
  });

  const waitBeforeRetry = new sfn.Wait(scope, `${idPrefix}WaitBeforeRetry`, {
    time: sfn.WaitTime.duration(Duration.minutes(2)),
  });

  const maxRetriesExceeded = new sfn.Fail(scope, `${idPrefix}MaxRetriesExceeded`, {
    error: "MaxRetriesExceeded",
    cause: "Exceeded maximum retry attempts (10) for capacity/throttling errors",
  });

  const checkRetryLimit = new sfn.Choice(scope, `${idPrefix}CheckRetryLimit`)
    .when(sfn.Condition.numberGreaterThanEquals("$.retryCount", 10), maxRetriesExceeded)
    .otherwise(waitBeforeRetry);

  const retryChain = incrementRetry.next(checkRetryLimit);

  const nonCapacityFailParsed = new sfn.Fail(scope, `${idPrefix}NonCapacityFailureParsed`, {
    error: "NonCapacityFailure",
    causePath: sfn.JsonPath.stringAt("$.cause.Parsed.FailureReason"),
  });

  const nonCapacityFailString = new sfn.Fail(scope, `${idPrefix}NonCapacityFailureString`, {
    error: "NonCapacityFailure",
    causePath: sfn.JsonPath.stringAt("$.cause.Cause"),
  });

  const failureReasonUnknown = new sfn.Fail(scope, `${idPrefix}FailureReasonUnknown`, {
    error: "NonCapacityFailure",
    cause: "Unknown failure",
  });

  const checkFailureReasonPresent = new sfn.Choice(scope, `${idPrefix}CheckFailureReasonPresent`)
    .when(sfn.Condition.isNull("$.cause.Parsed.FailureReason"), failureReasonUnknown)
    .otherwise(nonCapacityFailParsed);

  const parseErrorCause = new sfn.Pass(scope, `${idPrefix}ParseErrorCause`, {
    parameters: {
      "Error.$": "$.cause.Error",
      "Cause.$": "$.cause.Cause",
      "Parsed.$": "States.StringToJson($.cause.Cause)",
    },
    resultPath: "$.cause",
  });

  const checkCapacityError = new sfn.Choice(scope, `${idPrefix}CheckCapacityError`)
    .when(sfn.Condition.stringMatches("$.cause.Parsed.FailureReason", "*CapacityError*"), retryChain)
    .otherwise(checkFailureReasonPresent);

  const checkThrottling = new sfn.Choice(scope, `${idPrefix}CheckThrottling`)
    .when(sfn.Condition.stringMatches("$.cause.Cause", "*ThrottlingException*"), retryChain)
    .otherwise(nonCapacityFailString);

  const routeCauseFormat = new sfn.Choice(scope, `${idPrefix}RouteCauseFormat`)
    .when(sfn.Condition.stringMatches("$.cause.Cause", "{*"), parseErrorCause)
    .otherwise(checkThrottling);

  // Prepare flattened input for the transform-job task
  const prepareBatchInput = new sfn.Pass(scope, `${idPrefix}PrepareBatchInput`, {
    parameters: {
      "instance_type.$": "$.input.instance_type",
      "input_prefix.$": "$.input.input_prefix",
      "output_prefix.$": "$.input.output_prefix",
      "retryCount.$": "$.retryCount",
      "input.$": "$.input",
    },
    resultPath: "$",
  });

  batchTask.addCatch(routeCauseFormat, {
    errors: ["States.TaskFailed"],
    resultPath: "$.cause",
  });

  parseErrorCause.next(checkCapacityError);
  waitBeforeRetry.next(prepareBatchInput);

  return { initRetryCounter, prepareBatchInput, batchTask };
};

export interface DotsOcrBatchProps {
  modelName: string;   // SageMaker model to use
  inputBucket: string; // where PDFs/images live
  outputBucket: string; // where JSON results go
  jobNameBase?: string;
}

/**
 * DotsOCR Batch Transform (binary objects):
 *  - Reads each S3 object under input_prefix (no splitting)
 *  - Sends raw bytes to /invocations
 *  - Writes one JSON per input object under output_prefix
 */
export const createDotsOcrBatchStateMachine = (
  scope: Construct,
  id: string,
  props: DotsOcrBatchProps
): sfn.StateMachine => {
  const { modelName, inputBucket, outputBucket, jobNameBase = modelName } = props;

  // 1) Inject defaults
  const injectDefaults = new sfn.Pass(scope, `${id}InjectDefaults`, {
    result: sfn.Result.fromObject(DEFAULT_PARAMS),
    resultPath: "$.defaults",
  });

  // 2) Merge caller input with defaults
  const mergeParams = new sfn.Pass(scope, `${id}MergeParams`, {
    parameters: { "merged.$": "States.JsonMerge($.defaults, $, false)" },
    resultPath: "$",
  });

  // 3) Match your tested pattern: put resolved params at the TOP LEVEL
  const selectParams = new sfn.Pass(scope, `${id}SelectParams`, {
    parameters: {
      "instance_type.$": "$.merged.instance_type",
      "input_prefix.$": "$.merged.input_prefix",
      "output_prefix.$": "$.merged.output_prefix",
    },
    resultPath: "$",
  });

  // 4) Batch Transform (binary)
  const batchTask = new tasks.SageMakerCreateTransformJob(scope, `${id}Transform`, {
    transformJobName: buildTransformJobName(jobNameBase, "dots"),
    modelName,
    integrationPattern: sfn.IntegrationPattern.RUN_JOB,
    batchStrategy: tasks.BatchStrategy.SINGLE_RECORD, // not used with NONE, but ok
    transformInput: {
      transformDataSource: {
        s3DataSource: {
          s3DataType: tasks.S3DataType.S3_PREFIX,
          s3Uri: sfn.JsonPath.format(
            "s3://{}/{}",
            inputBucket,
            sfn.JsonPath.stringAt("$.input.input_prefix") // after initRetryCounter -> $.input.*
          ),
        },
      },
      contentType: "application/octet-stream", // PDFs/images as raw bytes
      splitType: tasks.SplitType.NONE,         // one object = one request
    },
    transformOutput: {
      s3OutputPath: sfn.JsonPath.format(
        "s3://{}/{}",
        outputBucket,
        sfn.JsonPath.stringAt("$.input.output_prefix")
      ),
      accept: "application/json",
    },
    transformResources: {
      instanceCount: 1,
      instanceType: new ec2.InstanceType(sfn.JsonPath.stringAt("$.input.instance_type")),
    },
  });

  // 5) Same retry block you use elsewhere
  const retry = createRetryLogic(scope, `${id}`, batchTask);

  // Definition
  return new sfn.StateMachine(scope, `${id}StateMachine`, {
    definition: injectDefaults
      .next(mergeParams)
      .next(selectParams)
      .next(retry.initRetryCounter)  // copies WHOLE state to $.input (your pattern)
      .next(retry.prepareBatchInput) // flattens to the shape the task expects
      .next(retry.batchTask),
    timeout: Duration.hours(2),
  });
};
