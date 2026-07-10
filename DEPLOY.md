# 小 ro 导购 - 服务器部署指南

## 一、服务器要求

- 系统：Linux（推荐 Ubuntu 20.04+）
- Python：3.9+
- 内存：至少 2GB
- 端口：8000（可修改）

## 二、部署步骤

### 1. 上传项目到服务器

```bash
# 在本地压缩项目
cd /Users/xulindi/ecommerce-agent
tar -czf xiaoro-shopping.tar.gz backend/

# 上传到服务器（替换 your-server-ip）
scp xiaoro-shopping.tar.gz user@your-server-ip:/home/user/

# 登录服务器
ssh user@your-server-ip
```

### 2. 服务器上解压并安装

```bash
# 解压
cd /home/user
tar -xzf xiaoro-shopping.tar.gz
cd backend

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
# 创建配置文件
cat > .env << 'ENV'
# 数据库配置
DATABASE_URL=postgresql://user:password@localhost:5432/xiaoro

# API配置
API_KEY=your_api_key_here

# 环境设置
ENVIRONMENT=production
DEBUG=false
ENV
```

### 4. 启动服务

```bash
# 方式一：直接启动（测试）
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 方式二：使用 gunicorn（生产）
pip install gunicorn
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

### 5. 使用 systemd 守护进程

```bash
# 创建服务文件
sudo cat > /etc/systemd/system/xiaoro.service << 'SERVICE'
[Unit]
Description=Xiao Ro Shopping Assistant
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/home/user/backend
Environment="PATH=/home/user/backend/venv/bin"
ExecStart=/home/user/backend/venv/bin/gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE

# 启动服务
sudo systemctl daemon-reload
sudo systemctl start xiaoro
sudo systemctl enable xiaoro

# 查看状态
sudo systemctl status xiaoro
```

## 三、访问地址

部署成功后，访问：
```
http://your-server-ip:8000/chat
```

## 四、配置域名（可选）

### 使用 Nginx 反向代理

```bash
# 安装 nginx
sudo apt install nginx

# 配置反向代理
sudo cat > /etc/nginx/sites-available/xiaoro << 'NGINX'
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
NGINX

# 启用配置
sudo ln -s /etc/nginx/sites-available/xiaoro /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 配置 HTTPS（可选）

```bash
# 安装 certbot
sudo apt install certbot python3-certbot-nginx

# 获取证书
sudo certbot --nginx -d your-domain.com
```

## 五、常用命令

```bash
# 查看日志
sudo journalctl -u xiaoro -f

# 重启服务
sudo systemctl restart xiaoro

# 停止服务
sudo systemctl stop xiaoro

# 更新代码后重启
cd /home/user/backend
git pull
sudo systemctl restart xiaoro
```
