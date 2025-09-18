#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { DotsOcrSagemakerStack } from "../lib/dotsocr-sagemaker-stack";

const app = new cdk.App();

// You can override these via env vars when deploying.
const config = {
  dependencies: {
    // Must be globally unique if you keep explicit names
    modelBucketName: process.env.MODEL_BUCKET_NAME || "dotsocr-models-bucket",
    dotsOcrModelName: process.env.DOTSOCR_MODEL_NAME || "DotsOCR",
    dotsOcrS3Key: process.env.DOTSOCR_S3_KEY || "models/DotsOCR.tar.gz",

    inputBucketName: process.env.INPUT_BUCKET_NAME || "dotsocr-input-documents",
    outputBucketName: process.env.OUTPUT_BUCKET_NAME || "dotsocr-processed-results",
  },

  // CDK env (defaults to your current AWS profile)
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || "eu-west-2",
  },
};

new DotsOcrSagemakerStack(app, "DotsOcrSagemakerStack", config);

app.synth();
