FROM alpine:3.20
RUN apk add --no-cache ffmpeg python3
COPY docker/feeder.sh /feeder.sh
ENTRYPOINT ["/bin/sh", "/feeder.sh"]
