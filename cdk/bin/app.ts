#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { DotsOcrSagemakerStack } from "../lib/dotsocr-sagemaker-stack";

const app = new cdk.App();

// You can override these via env vars when deploying.
const config = {
  dependencies: {
    // Must be globally unique if you keep explicit names
    modelBucketName: "dotsocr-models-bucket",
    dotsOcrModelName: "DotsOCR",
    dotsOcrS3Key: "models/DotsOCR.tar.gz",
    inputBucketName: "dotsocr-input-documents",
    outputBucketName: "dotsocr-processed-results",
  },

  // CDK env (defaults to your current AWS profile)
  env: {
    account: "597088029880",
    region: "eu-west-2",
  },
};

new DotsOcrSagemakerStack(app, "DotsOcrSagemakerStack", config);

app.synth();
