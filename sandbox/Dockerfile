# 轻量级 JSON 编辑沙盒
# 用于安全执行 Claude 返回的 CLI 命令

FROM alpine:3.19

# 安装必要工具
RUN apk add --no-cache \
    jq \
    bash \
    coreutils \
    sed \
    grep \
    && rm -rf /var/cache/apk/*

# Fixed unprivileged runtime identity. Docker also enforces this with --user so
# image drift cannot silently restore root execution.
RUN addgroup -g 65532 sandbox \
    && adduser -D -u 65532 -G sandbox sandbox \
    && mkdir -p /workspace \
    && chown -R 65532:65532 /workspace

# 创建工作目录
WORKDIR /workspace
USER 65532:65532

# 默认保持容器运行
CMD ["tail", "-f", "/dev/null"]

