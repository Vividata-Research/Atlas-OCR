


docker run --rm --gpus '"device=0"' \
  -p 8080:8080 -p 8081:8081 \
  -v "$(realpath ./weights/DotsOCR)":/models/DotsOCR:ro \
  -v "$(realpath ./output_data)":/app/output \
  -e MODEL_PATH=/models/DotsOCR \
  -e CUDA_VISIBLE_DEVICES=0 \
  dotsocr:dev



./batch-local.sh input_data output_data http://localhost:8080
