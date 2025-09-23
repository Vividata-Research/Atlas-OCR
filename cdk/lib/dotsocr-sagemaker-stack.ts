import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as sagemaker from "aws-cdk-lib/aws-sagemaker";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as path from "path";
import { DockerImageAsset, Platform } from "aws-cdk-lib/aws-ecr-assets";
import { createDotsOcrBatchStateMachine } from "../helpers/dotsOcrBatch";

interface StackDependencyList {
  modelBucketName: string;     
  modelName: string;           
  dotsOcrS3Key: string;        
  inputBucketName: string;     
  outputBucketName: string;    
}

interface Config extends cdk.StackProps {
  dependencies: StackDependencyList;
}

export class DotsOcrSagemakerStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: Config) {
    super(scope, id, props);

    // ────────────────────────────────────────────────────────────
    // S3 buckets
    // ────────────────────────────────────────────────────────────
    const modelBucket = new s3.Bucket(this, "DotsOcrModelBucket", {
      bucketName: props.dependencies.modelBucketName,
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const inputBucket = new s3.Bucket(this, "DotsOcrInputBucket", {
      bucketName: props.dependencies.inputBucketName,
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [{ id: "DeleteOldInputs", expiration: cdk.Duration.days(30) }],
    });

    const outputBucket = new s3.Bucket(this, "DotsOcrOutputBucket", {
      bucketName: props.dependencies.outputBucketName,
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ────────────────────────────────────────────────────────────
    // ECR repo that will store BuildKit cache (separate from CDK asset repo)
    // ────────────────────────────────────────────────────────────
    const cacheRepo = new ecr.Repository(this, "DotsOcrDockerCacheRepo", {
      repositoryName: "dotsocr-build-cache",
      imageScanOnPush: false,
      encryption: ecr.RepositoryEncryption.KMS,
      lifecycleRules: [
        // keep cache lean over time (tweak to taste)
        { tagPrefixList: ["cache"], maxImageCount: 10 },
      ],
    });
    const cacheRef = `${cacheRepo.repositoryUri}:cache`;

    // ────────────────────────────────────────────────────────────
    // Docker image asset (CDK-managed ECR repo) + BuildKit cache in ECR
    // ────────────────────────────────────────────────────────────
    const imageAsset = new DockerImageAsset(this, "DotsOcrImage", {
      directory: path.join(__dirname, "../../container"),
      platform: Platform.LINUX_AMD64,
    });

    // ────────────────────────────────────────────────────────────
    // SageMaker execution role (used BY the container at runtime)
    // ────────────────────────────────────────────────────────────
    const modelRole = new iam.Role(this, "DotsOcrModelRole", {
      assumedBy: new iam.ServicePrincipal("sagemaker.amazonaws.com"),
      inlinePolicies: {
        DotsOcrPermissions: new iam.PolicyDocument({
          statements: [
            // Pull from ECR
            new iam.PolicyStatement({
              actions: [
                "ecr:GetAuthorizationToken",
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
              ],
              resources: ["*"],
            }),
            // Read the weights tarball (ModelDataUrl)
            new iam.PolicyStatement({
              actions: ["s3:GetObject", "s3:ListBucket"],
              resources: [modelBucket.bucketArn, `${modelBucket.bucketArn}/*`],
            }),
            // Read batch inputs + write batch outputs
            new iam.PolicyStatement({
              actions: ["s3:GetObject", "s3:ListBucket", "s3:PutObject", "s3:DeleteObject"],
              resources: [
                inputBucket.bucketArn,
                `${inputBucket.bucketArn}/*`,
                outputBucket.bucketArn,
                `${outputBucket.bucketArn}/*`,
              ],
            }),
            // Logs
            new iam.PolicyStatement({
              actions: [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
                "logs:DescribeLogGroups",
                "logs:DescribeLogStreams",
              ],
              resources: ["*"],
            }),
          ],
        }),
      },
    });

    // allow pulling this image
    imageAsset.repository.grantPull(modelRole);

    // ────────────────────────────────────────────────────────────
    // SageMaker Model (weights via ModelDataUrl → /opt/ml/model)
    // ────────────────────────────────────────────────────────────
    const model = new sagemaker.CfnModel(this, "DotsOcrModel", {
      executionRoleArn: modelRole.roleArn,
      primaryContainer: {
        image: imageAsset.imageUri,
        // SageMaker extracts this tar.gz into /opt/ml/model/
        modelDataUrl: `s3://${props.dependencies.modelBucketName}/${props.dependencies.dotsOcrS3Key}`,
        environment: {
          // vLLM + app expect weights at /opt/ml/model/DotsOCR
          MODEL_PATH: "/opt/ml/model/DotsOCR",
          VLLM_PORT: "8081",
          GPU_MEMORY_UTILIZATION: "0.95",
          TENSOR_PARALLEL_SIZE: "1",
          HEALTH_CHECK_TIMEOUT: "30",
          PYTHONUNBUFFERED: "1",
        },
      },
    });

    // Build the Step Functions state machine that runs Batch Transform on this model
    const batchSm = createDotsOcrBatchStateMachine(this, "DotsOcrBatch", {
      modelName: model.attrModelName,
      inputBucket: inputBucket.bucketName,
      outputBucket: outputBucket.bucketName,
      jobNameBase: props.dependencies.modelName,
    });

    // ────────────────────────────────────────────────────────────
    // Outputs
    // ────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, "ModelName", { value: model.attrModelName });
    new cdk.CfnOutput(this, "DockerImageUri", { value: imageAsset.imageUri });
    new cdk.CfnOutput(this, "ModelBucketName", { value: modelBucket.bucketName });
    new cdk.CfnOutput(this, "InputBucketName", { value: inputBucket.bucketName });
    new cdk.CfnOutput(this, "OutputBucketName", { value: outputBucket.bucketName });
    new cdk.CfnOutput(this, "DotsOcrBatchStateMachineArn", { value: batchSm.stateMachineArn });
    new cdk.CfnOutput(this, "DockerCacheRef", { value: cacheRef });
  }
}