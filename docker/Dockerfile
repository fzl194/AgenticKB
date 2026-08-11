# ── CoreMasterKB All-in-One ──────────────────────────────────────────
# 多阶段构建：Stage 1-2 编译，Stage 3 只留运行时

# ═══════════════════════════════════════════════════════════════
# Stage 1: 构建 Java (Maven 用完即丢)
# ═══════════════════════════════════════════════════════════════
FROM eclipse-temurin:21-jdk AS build-java
# ── Maven 从华为云镜像下载 ──
RUN curl -fsSL https://repo.huaweicloud.com/apache/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.tar.gz \
    | tar xz -C /opt && ln -s /opt/apache-maven-3.9.9/bin/mvn /usr/bin/mvn
# ── Maven 依赖换阿里云镜像 ──
RUN mkdir -p /root/.m2 && echo '<settings><mirrors><mirror><id>aliyun</id><mirrorOf>*</mirrorOf><url>https://maven.aliyun.com/repository/public</url></mirror></mirrors></settings>' > /root/.m2/settings.xml
WORKDIR /build
COPY agent_serving_java/pom.xml .
RUN mvn dependency:go-offline -B
COPY agent_serving_java/src ./src
RUN mvn package -DskipTests -B -q

# ═══════════════════════════════════════════════════════════════
# Stage 2: 构建前端 (Node 用完即丢)
# ═══════════════════════════════════════════════════════════════
FROM node:20-alpine AS build-ui
# ── npm 换淘宝镜像 ──
RUN npm config set registry https://registry.npmmirror.com
WORKDIR /build
COPY kb-ui/package.json kb-ui/package-lock.json* kb-ui/pnpm-lock.yaml* kb-ui/yarn.lock* ./
RUN npm ci --silent 2>/dev/null || npm install --silent
COPY kb-ui/ .
RUN npm run build

# ═══════════════════════════════════════════════════════════════
# Stage 3: 运行时镜像 (只保留运行必需的)
# ═══════════════════════════════════════════════════════════════
FROM python:3.11-slim

# ── 换国内 Debian 源加速 ──
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources

# ── 运行时系统依赖 ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libpq-dev supervisor nginx \
    openjdk-21-jre-headless p7zip-full libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

# 构建期验证：旧版 .doc 转换依赖必须存在且可以无界面启动
RUN command -v libreoffice >/dev/null \
    && libreoffice --headless --version \
    || (echo "错误：未找到 LibreOffice，无法解析 .doc 文件" >&2; exit 1)

WORKDIR /app

# ── Python 依赖（仅运行时，不含 dev） ──
COPY pyproject.toml ./
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    "fastapi>=0.110" \
    "uvicorn[standard]>=0.29" \
    "python-multipart>=0.0.9" \
    "pydantic>=2.7" \
    "pydantic-settings>=2.3" \
    "aiosqlite>=0.20" \
    "PyYAML>=6.0" \
    "markdown-it-py>=3.0" \
    "jinja2>=3.1" \
    "jsonschema>=4.21" \
    "httpx>=0.27" \
    "psycopg[binary,pool]>=3.1" \
    "pdfminer.six>=20231228" \
    "python-docx>=1.1" \
    "openpyxl>=3.1,<4" \
    "xlrd>=2.0,<3" \
    "jieba>=0.42" \
    "fastmcp>=2.0"

# 构建期验证：Excel 解析依赖必须可以导入
RUN python -c "import openpyxl, xlrd; print(f'Excel 解析依赖验证通过：openpyxl={openpyxl.__version__}，xlrd={xlrd.__version__}')" \
    || (echo "错误：openpyxl 或 xlrd 未正确安装，无法解析 Excel 文件" >&2; exit 1)

# ── 从构建阶段复制产物 ──
COPY --from=build-java /build/target/*.jar /app/agent_serving.jar
COPY --from=build-ui /build/dist /app/kb-ui-dist

# ── Python 服务代码 ──
COPY knowledge_mining/ ./knowledge_mining/
COPY llm_service/ ./llm_service/
COPY main_control_service/ ./main_control_service/
COPY mcp_server/ ./mcp_server/

# ── 共享配置与数据 ──
# 域配置（domain_registry.yaml + scenario_packs/）不在这里 COPY：
# 它们随 main_control_service/ 一起进镜像，是 Python 与 Java 共用的唯一真相源。
# 镜像只携带配置模板；生产 .env 由部署主机挂载
COPY .env.example ./
COPY reset_db.py ./
COPY databases/ ./databases/

# ── nginx + supervisord 配置 ──
COPY docker/nginx.conf /etc/nginx/sites-available/default
RUN rm -f /etc/nginx/sites-enabled/default \
    && ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
COPY docker/supervisord.conf /etc/supervisor/conf.d/cmkb.conf

# /app/logs 通常由 docker-compose 的 ./logs 挂载覆盖；这里仍然建出来，
# 保证不挂卷直接 docker run 时 supervisor 也能写日志（否则会启动失败）。
RUN mkdir -p /app/uploads /app/data/main_control_service /app/logs

EXPOSE 80 8900 8901 8910 8081 9000

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf", "-n"]
