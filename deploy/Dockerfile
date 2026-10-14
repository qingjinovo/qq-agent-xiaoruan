# QQ Agent 无头容器镜像
#
# 构建上下文 = 仓库根目录（因为要 COPY app/ 下的源码）
#   docker build -f deploy/Dockerfile -t qq-agent:local .
#
# 说明：src/app.js 在入仓前已打补丁读 QQ_AGENT_BIND_HOST，
#      容器里必须监听 0.0.0.0 才能让宿主机的端口映射生效。
FROM node:22-slim

WORKDIR /app

# 依赖层单独缓存（本项目生产依赖只有三个纯 JS 包，无原生编译）
COPY app/package.json ./
RUN npm install --omit=dev --no-audit --no-fund

# 应用本体
COPY app/src/ ./src/
COPY app/ui/ ./ui/
COPY app/assets/ ./assets/
COPY app/README.md ./

ENV QQ_AGENT_DATA_DIR=/app/data
ENV QQ_AGENT_BIND_HOST=0.0.0.0
RUN mkdir -p /app/data

EXPOSE 3210

# 无头入口（不带 Electron 窗口，控制台走浏览器）
CMD ["node", "src/server.js"]
