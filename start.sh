#!/bin/bash

# ==================== 电商智能导购系统 - 启动脚本 ====================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==================== 函数定义 ====================

print_header() {
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}    电商智能导购系统 - 启动脚本${NC}"
    echo -e "${BLUE}============================================${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# ==================== 检查依赖 ====================

check_dependencies() {
    print_info "检查系统依赖..."

    # 检查Docker
    if ! command -v docker &> /dev/null; then
        print_error "Docker未安装，请先安装Docker"
        echo "安装地址: https://docs.docker.com/get-docker/"
        exit 1
    fi
    print_success "Docker已安装"

    # 检查Docker Compose
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        print_error "Docker Compose未安装"
        echo "安装地址: https://docs.docker.com/compose/install/"
        exit 1
    fi
    print_success "Docker Compose已安装"

    echo ""
}

# ==================== 环境变量检查 ====================

check_env() {
    print_info "检查环境变量..."

    if [ ! -f .env ]; then
        print_warning ".env文件不存在"
        if [ -f .env.example ]; then
            print_info "从.env.example创建.env文件..."
            cp .env.example .env
        else
            print_error ".env.example文件不存在"
            exit 1
        fi
    fi

    # 检查必需的环境变量
    source .env

    if [ -z "$DOUBAO_API_KEY" ] && [ -z "$KIMI_API_KEY" ]; then
        print_warning "未配置API密钥，AI功能可能不可用"
    fi

    print_success "环境变量检查完成"
    echo ""
}

# ==================== 启动服务 ====================

start_services() {
    print_header

    local mode=${1:-"dev"}

    case $mode in
        dev)
            print_info "启动开发环境..."
            docker-compose -f docker-compose.yml up -d
            ;;
        prod)
            print_info "启动生产环境..."
            docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
            ;;
        local)
            print_info "启动本地环境（不使用Docker）..."
            python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
            ;;
        *)
            print_error "未知模式: $mode"
            echo "用法: ./start.sh [dev|prod|local]"
            exit 1
            ;;
    esac
}

# ==================== 停止服务 ====================

stop_services() {
    print_info "停止所有服务..."
    docker-compose down
    print_success "服务已停止"
}

# ==================== 查看日志 ====================

view_logs() {
    local service=${1:-""}
    if [ -z "$service" ]; then
        docker-compose logs -f
    else
        docker-compose logs -f "$service"
    fi
}

# ==================== 健康检查 ====================

health_check() {
    print_info "检查服务健康状态..."

    # 检查FastAPI
    if curl -s http://localhost:8000/health > /dev/null; then
        print_success "FastAPI服务正常"
    else
        print_error "FastAPI服务异常"
    fi

    # 检查Redis
    if docker exec ecommerce_redis redis-cli ping > /dev/null 2>&1; then
        print_success "Redis服务正常"
    else
        print_error "Redis服务异常"
    fi

    # 检查PostgreSQL
    if docker exec ecommerce_postgres pg_isready -U postgres > /dev/null 2>&1; then
        print_success "PostgreSQL服务正常"
    else
        print_error "PostgreSQL服务异常"
    fi

    # 检查Celery Worker
    if docker exec ecommerce_celery_worker celery -A app.tasks.worker inspect active > /dev/null 2>&1; then
        print_success "Celery Worker正常"
    else
        print_warning "Celery Worker可能未启动"
    fi

    echo ""
    print_info "访问地址："
    echo "  - API文档: http://localhost:8000/docs"
    echo "  - 聊天页面: http://localhost:8000/chat"
    echo "  - Flower监控: http://localhost:5555"
    echo ""
}

# ==================== 主菜单 ====================

show_menu() {
    echo ""
    echo "请选择操作："
    echo "  1) 启动开发环境"
    echo "  2) 停止服务"
    echo "  3) 查看日志"
    echo "  4) 健康检查"
    echo "  5) 重启服务"
    echo "  6) 本地运行（无Docker）"
    echo "  0) 退出"
    echo ""
}

# ==================== 主程序 ====================

main() {
    case "${1:-}" in
        start|dev)
            check_dependencies
            check_env
            start_services dev
            health_check
            ;;
        stop)
            stop_services
            ;;
        logs)
            view_logs "${2:-}"
            ;;
        health|check)
            health_check
            ;;
        restart)
            stop_services
            sleep 2
            start_services dev
            health_check
            ;;
        local)
            print_info "启动本地开发服务器..."
            python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
            ;;
        menu|"")
            while true; do
                print_header
                show_menu
                read -p "请输入选项 [0-6]: " choice
                case $choice in
                    1|start|dev) main start ;;
                    2|stop) main stop ;;
                    3|logs) read -p "输入服务名(留空查看全部): " svc; main logs "$svc" ;;
                    4|health|check) main health ;;
                    5|restart) main restart ;;
                    6|local) main local ;;
                    0|exit|quit) echo "再见！"; exit 0 ;;
                    *) print_error "无效选项" ;;
                esac
            done
            ;;
        *)
            echo "用法: $0 {start|stop|logs|health|restart|local|menu}"
            exit 1
            ;;
    esac
}

main "$@"
