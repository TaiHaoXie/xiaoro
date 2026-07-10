# ==================== 基础镜像 ====================
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# ==================== 系统依赖 ====================
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ==================== Python依赖 ====================
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ==================== Celery依赖 ====================
RUN pip install --no-cache-dir \
    celery[redis]==5.3.0 \
    redis==5.0.0 \
    flower==2.0.1

# ==================== 复制应用代码 ====================
COPY ./app ./app
COPY ./static ./static

# ==================== 创建非root用户 ====================
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

# ==================== 暴露端口 ====================
EXPOSE 8000

# ==================== 启动命令 ====================
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
