// cdk/helpers/dotsOcrBatch.ts
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";

// Version tag for job names
export const DEPLOYMENT_VERSION = "v1";

// Defaults if the execution input omits fields.
// input_prefix/output_prefix are *prefixes inside the buckets* passed by the stack.
export const DEFAULT_PARAMS = {
  instance_type: "ml.g5.xlarge",
  input_prefix: "incoming/",          // e.g. put PDFs at s3://dotsocr-input-documents/incoming/...
  output_prefix: "processed/runs/1/", // results at s3://dotsocr-processed-results/processed/runs/1/...
} as const;

/** Build a unique transform job name (<= ~63 chars is safe) */
export const buildTransformJobName = (base: string) =>
  sfn.JsonPath.format(
    "{}-ocr-{}-{}",
    base.substring(0, 20),
    DEPLOYMENT_VERSION,
    sfn.JsonPath.stringAt("$$.Execution.Name"),
  );

/** Retry logic for capacity/throttling with explicit counter management (unchanged) */
export const createRetryLogic = (
  scope: Construct,
  idPrefix: string,
  batchTask: tasks.SageMakerCreateTransformJob
) => {
  const initRetryCounter = new sfn.Pass(scope, `${idPrefix}InitRetryCounter`, {
    parameters: { retryCount: 0, "input.$": "$" },
    resultPath: "$",
  });

  const incrementRetry = new sfn.Pass(scope, `${idPrefix}IncrementRetry`, {
    parameters: { "retryCount.$": "States.MathAdd($.retryCount, 1)", "input.$": "$.input" },
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

  // Parse JSON-ish error Causes when available
  const parseErrorCause = new sfn.Pass(scope, `${idPrefix}ParseErrorCause`, {
    parameters: {
      "Error.$": "$.cause.Error",
      "Cause.$": "$.cause.Cause",
      "Parsed.$": "States.StringToJson($.cause.Cause)",
    },
    resultPath: "$.cause",
  });

  const nonCapacityFailParsed = new sfn.Fail(scope, `${idPrefix}NonCapacityFailureParsed`, {
    error: "NonCapacityFailure",
    causePath: sfn.JsonPath.stringAt("$.cause.Parsed.FailureReason"),
  });

  const failureReasonUnknown = new sfn.Fail(scope, `${idPrefix}FailureReasonUnknown`, {
    error: "NonCapacityFailure",
    cause: "Unknown failure",
  });

  const checkFailureReasonPresent = new sfn.Choice(scope, `${idPrefix}CheckFailureReasonPresent`)
    .when(sfn.Condition.isNull("$.cause.Parsed.FailureReason"), failureReasonUnknown)
    .otherwise(nonCapacityFailParsed);

  const checkCapacityError = new sfn.Choice(scope, `${idPrefix}CheckCapacityError`)
    .when(
      sfn.Condition.stringMatches("$.cause.Parsed.FailureReason", "*CapacityError*"),
      retryChain
    )
    .otherwise(checkFailureReasonPresent);

  const nonCapacityFailString = new sfn.Fail(scope, `${idPrefix}NonCapacityFailureString`, {
    error: "NonCapacityFailure",
    causePath: sfn.JsonPath.stringAt("$.cause.Cause"),
  });

  const checkThrottling = new sfn.Choice(scope, `${idPrefix}CheckThrottling`)
    .when(sfn.Condition.stringMatches("$.cause.Cause", "*ThrottlingException*"), retryChain)
    .otherwise(nonCapacityFailString);

  const routeCauseFormat = new sfn.Choice(scope, `${idPrefix}RouteCauseFormat`)
    .when(sfn.Condition.stringMatches("$.cause.Cause", "{*"), parseErrorCause)
    .otherwise(checkThrottling);

  // Prepare flattened input for the task
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

  waitBeforeRetry.next(prepareBatchInput);
  parseErrorCause.next(checkCapacityError);

  return { initRetryCounter, prepareBatchInput, batchTask };
};

export interface DotsOcrBatchProps {
  /** The SageMaker *Model* name to use for transform jobs (model.attrModelName) */
  modelName: string;
  /** S3 bucket names for IO (stack passes these in) */
  inputBucket: string;
  outputBucket: string;
  /** Base name used in transformJobName */
  jobNameBase?: string; // defaults to modelName
}

/**
 * Batch Transform for PDFs/images (binary):
 *  - Reads each object under `s3://inputBucket/input_prefix/` as a single request (splitType=None)
 *  - Posts raw bytes to your container's /invocations
 *  - Writes one JSON result per object under `s3://outputBucket/output_prefix/`
 *
 * Execution input example:
 * {
 *   "input_prefix":  "incoming/company_id=123/job_id=abc/",
 *   "output_prefix": "processed/company_id=123/job_id=abc/",
 *   "instance_type": "ml.g5.xlarge"
 * }
 */
export const createDotsOcrBatchStateMachine = (
  scope: Construct,
  id: string,
  props: DotsOcrBatchProps
): sfn.StateMachine => {
  const { modelName, inputBucket, outputBucket, jobNameBase = modelName } = props;

  // Inject defaults -> $.defaults
  const injectDefaults = new sfn.Pass(scope, `${id}InjectDefaults`, {
    result: sfn.Result.fromObject(DEFAULT_PARAMS),
    resultPath: "$.defaults",
  });

  // Merge caller input with defaults -> $.merged
  const mergeParams = new sfn.Pass(scope, `${id}MergeParams`, {
    parameters: { "merged.$": "States.JsonMerge($.defaults, $, false)" },
    resultPath: "$",
  });

  // Select final params -> $.input
  const selectParams = new sfn.Pass(scope, `${id}SelectParams`, {
    parameters: {
      "input.instance_type.$": "$.merged.instance_type",
      "input.input_prefix.$": "$.merged.input_prefix",
      "input.output_prefix.$": "$.merged.output_prefix",
    },
    resultPath: "$",
  });

  // Batch Transform task configured for *binary* objects (PDF/image)
  const batchTask = new tasks.SageMakerCreateTransformJob(scope, `${id}Transform`, {
    transformJobName: buildTransformJobName(jobNameBase),
    modelName,
    integrationPattern: sfn.IntegrationPattern.RUN_JOB,
    batchStrategy: tasks.BatchStrategy.SINGLE_RECORD, // fine; not used with splitType=None
    transformInput: {
      transformDataSource: {
        s3DataSource: {
          s3DataType: tasks.S3DataType.S3_PREFIX,
          s3Uri: sfn.JsonPath.format(
            "s3://{}/{}",
            inputBucket,
            sfn.JsonPath.stringAt("$.input.input_prefix")
          ),
        },
      },
      // IMPORTANT for PDFs/images:
      contentType: "application/octet-stream", // allows PDF/JPG/PNG/etc.; container detects type
      splitType: tasks.SplitType.NONE,         // one object -> one request
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

  const retry = createRetryLogic(scope, `${id}`, batchTask);

  return new sfn.StateMachine(scope, `${id}StateMachine`, {
    definition: injectDefaults
      .next(mergeParams)
      .next(selectParams)
      .next(retry.initRetryCounter)
      .next(retry.prepareBatchInput)
      .next(retry.batchTask),
    timeout: Duration.hours(2),
  });
};
