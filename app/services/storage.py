"""
MinIO 对象存储服务

处理图片上传、下载、删除等操作
"""

from typing import Optional, BinaryIO, List
from pathlib import Path
import io
import logging
from datetime import timedelta

try:
    from minio import Minio
    from minio.error import S3Error
    MINIO_AVAILABLE = True
except ImportError:
    MINIO_AVAILABLE = False
    Minio = None
    S3Error = None

from app.config import settings

logger = logging.getLogger(__name__)


class MinIOService:
    """
    MinIO 对象存储服务

    功能：
    1. 上传文件
    2. 下载文件
    3. 生成预签名URL
    4. 删除文件
    5. 列出文件
    """

    # Bucket 名称
    BUCKET_PRODUCTS = "products"          # 商品图片
    BUCKET_USER_UPLOADS = "uploads"       # 用户上传
    BUCKET_DOCUMENTS = "documents"        # 文档

    def __init__(self):
        """初始化 MinIO 客户端"""
        if not MINIO_AVAILABLE:
            logger.warning("⚠️ MinIO 库未安装，使用本地存储模拟")
            self.client = None
            self.use_local = True
            return

        try:
            self.client = Minio(
                endpoint=settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=False  # 开发环境使用HTTP
            )
            self.use_local = False

            # 确保buckets存在
            self._ensure_buckets()

            logger.info(f"✅ MinIO客户端已连接: {settings.MINIO_ENDPOINT}")

        except Exception as e:
            logger.warning(f"⚠️ MinIO连接失败，使用本地存储模拟: {e}")
            self.client = None
            self.use_local = True

    def _ensure_buckets(self):
        """确保所有buckets存在"""
        if self.use_local or not self.client:
            return

        buckets = [
            self.BUCKET_PRODUCTS,
            self.BUCKET_USER_UPLOADS,
            self.BUCKET_DOCUMENTS
        ]

        for bucket in buckets:
            try:
                if not self.client.bucket_exists(bucket):
                    self.client.make_bucket(bucket)
                    logger.info(f"✅ 创建Bucket: {bucket}")

                    # 设置bucket策略（公开读取）
                    self._set_public_policy(bucket)
                else:
                    logger.info(f"ℹ️  Bucket已存在: {bucket}")

            except S3Error as e:
                logger.error(f"❌ Bucket操作失败 {bucket}: {e}")

    def _set_public_policy(self, bucket: str):
        """设置Bucket为公开读取"""
        policy = f'''{{
    "Version": "2012-10-17",
    "Statement": [
        {{
            "Effect": "Allow",
            "Principal": {{
                "AWS": ["*"]
            }},
            "Action": ["s3:GetObject"],
            "Resource": ["arn:aws:s3:::{bucket}/*"]
        }}
    ]
}}'''

        try:
            self.client.set_bucket_policy(bucket, policy)
            logger.info(f"✅ Bucket公开策略已设置: {bucket}")
        except S3Error as e:
            logger.warning(f"设置公开策略失败: {e}")

    # ==================== 上传操作 ====================

    async def upload_file(
        self,
        file_data: bytes,
        bucket: str,
        object_name: str,
        content_type: str = "application/octet-stream"
    ) -> bool:
        """
        上传文件

        Args:
            file_data: 文件二进制数据
            bucket: Bucket名称
            object_name: 对象名称（路径）
            content_type: Content-Type

        Returns:
            是否成功
        """
        if self.use_local:
            return self._upload_local(file_data, bucket, object_name)

        try:
            data_stream = io.BytesIO(file_data)
            self.client.put_object(
                bucket=bucket,
                object_name=object_name,
                data=data_stream,
                length=len(file_data),
                content_type=content_type
            )
            logger.info(f"✅ 文件已上传: {bucket}/{object_name}")
            return True

        except S3Error as e:
            logger.error(f"❌ 文件上传失败: {e}")
            return False

    async def upload_product_image(
        self,
        product_id: int,
        image_data: bytes,
        content_type: str = "image/jpeg"
    ) -> Optional[str]:
        """
        上传商品图片

        Args:
            product_id: 商品ID
            image_data: 图片数据
            content_type: 图片类型

        Returns:
            图片URL
        """
        # 确定文件扩展名
        ext = self._get_extension(content_type)
        object_name = f"products/{product_id}{ext}"

        success = await self.upload_file(
            file_data=image_data,
            bucket=self.BUCKET_PRODUCTS,
            object_name=object_name,
            content_type=content_type
        )

        if success:
            return self.get_public_url(bucket=self.BUCKET_PRODUCTS, object_name=object_name)

        return None

    async def upload_user_image(
        self,
        user_id: int,
        image_data: bytes,
        filename: str,
        content_type: str = "image/jpeg"
    ) -> Optional[str]:
        """
        上传用户图片（用于以图搜图）

        Args:
            user_id: 用户ID
            image_data: 图片数据
            filename: 文件名
            content_type: 图片类型

        Returns:
            图片URL
        """
        import time
        object_name = f"users/{user_id}/{int(time.time())}_{filename}"

        success = await self.upload_file(
            file_data=image_data,
            bucket=self.BUCKET_USER_UPLOADS,
            object_name=object_name,
            content_type=content_type
        )

        if success:
            return self.get_public_url(bucket=self.BUCKET_USER_UPLOADS, object_name=object_name)

        return None

    # ==================== 下载操作 ====================

    async def download_file(
        self,
        bucket: str,
        object_name: str
    ) -> Optional[bytes]:
        """
        下载文件

        Args:
            bucket: Bucket名称
            object_name: 对象名称

        Returns:
            文件数据
        """
        if self.use_local:
            return self._download_local(bucket, object_name)

        try:
            response = self.client.get_object(bucket, object_name)
            data = response.read()
            response.close()
            response.release_conn()
            return data

        except S3Error as e:
            logger.error(f"❌ 文件下载失败: {e}")
            return None

    async def download_product_image(self, product_id: int) -> Optional[bytes]:
        """
        下载商品图片

        Args:
            product_id: 商品ID

        Returns:
            图片数据
        """
        # 尝试多种可能的扩展名
        for ext in ['.jpg', '.jpeg', '.png', '.webp']:
            object_name = f"products/{product_id}{ext}"
            data = await self.download_file(self.BUCKET_PRODUCTS, object_name)
            if data:
                return data

        return None

    # ==================== URL生成 ====================

    def get_public_url(
        self,
        bucket: str,
        object_name: str
    ) -> str:
        """
        获取公开访问URL

        Args:
            bucket: Bucket名称
            object_name: 对象名称

        Returns:
            访问URL
        """
        if self.use_local:
            return f"/static/uploads/{bucket}/{object_name}"

        return f"http://{settings.MINIO_ENDPOINT}/{bucket}/{object_name}"

    def get_presigned_url(
        self,
        bucket: str,
        object_name: str,
        expires: timedelta = timedelta(hours=1)
    ) -> Optional[str]:
        """
        获取预签名URL（临时访问）

        Args:
            bucket: Bucket名称
            object_name: 对象名称
            expires: 过期时间

        Returns:
            预签名URL
        """
        if self.use_local or not self.client:
            return self.get_public_url(bucket, object_name)

        try:
            url = self.client.presigned_get_object(
                bucket_name=bucket,
                object_name=object_name,
                expires=expires
            )
            return url

        except S3Error as e:
            logger.error(f"❌ 预签名URL生成失败: {e}")
            return None

    # ==================== 删除操作 ====================

    async def delete_file(self, bucket: str, object_name: str) -> bool:
        """
        删除文件

        Args:
            bucket: Bucket名称
            object_name: 对象名称

        Returns:
            是否成功
        """
        if self.use_local:
            return self._delete_local(bucket, object_name)

        try:
            self.client.remove_object(bucket, object_name)
            logger.info(f"✅ 文件已删除: {bucket}/{object_name}")
            return True

        except S3Error as e:
            logger.error(f"❌ 文件删除失败: {e}")
            return False

    async def delete_product_image(self, product_id: int) -> bool:
        """
        删除商品图片

        Args:
            product_id: 商品ID

        Returns:
            是否成功
        """
        # 删除所有可能的扩展名
        for ext in ['.jpg', '.jpeg', '.png', '.webp']:
            object_name = f"products/{product_id}{ext}"
            await self.delete_file(self.BUCKET_PRODUCTS, object_name)

        return True

    # ==================== 列表操作 ====================

    async def list_files(
        self,
        bucket: str,
        prefix: str = "",
        recursive: bool = False
    ) -> List[str]:
        """
        列出文件

        Args:
            bucket: Bucket名称
            prefix: 前缀过滤
            recursive: 是否递归

        Returns:
            文件列表
        """
        if self.use_local:
            return self._list_local(bucket, prefix)

        try:
            objects = self.client.list_objects(
                bucket_name=bucket,
                prefix=prefix,
                recursive=recursive
            )
            return [obj.object_name for obj in objects]

        except S3Error as e:
            logger.error(f"❌ 文件列表获取失败: {e}")
            return []

    # ==================== 本地存储模拟（开发用）====================

    def _get_local_path(self, bucket: str, object_name: str) -> Path:
        """获取本地存储路径"""
        base_path = Path(__file__).parent.parent.parent / "data" / "uploads" / bucket
        base_path.mkdir(parents=True, exist_ok=True)
        return base_path / object_name

    def _upload_local(self, file_data: bytes, bucket: str, object_name: str) -> bool:
        """本地存储上传"""
        try:
            path = self._get_local_path(bucket, object_name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(file_data)
            logger.info(f"✅ 文件已保存到本地: {path}")
            return True
        except Exception as e:
            logger.error(f"❌ 本地保存失败: {e}")
            return False

    def _download_local(self, bucket: str, object_name: str) -> Optional[bytes]:
        """本地存储下载"""
        try:
            path = self._get_local_path(bucket, object_name)
            if path.exists():
                return path.read_bytes()
            return None
        except Exception as e:
            logger.error(f"❌ 本地读取失败: {e}")
            return None

    def _delete_local(self, bucket: str, object_name: str) -> bool:
        """本地存储删除"""
        try:
            path = self._get_local_path(bucket, object_name)
            if path.exists():
                path.unlink()
            return True
        except Exception as e:
            logger.error(f"❌ 本地删除失败: {e}")
            return False

    def _list_local(self, bucket: str, prefix: str = "") -> List[str]:
        """本地存储列表"""
        try:
            base_path = Path(__file__).parent.parent.parent / "data" / "uploads" / bucket
            if not base_path.exists():
                return []

            pattern = f"**/{prefix}*" if prefix else "**/*"
            return [
                str(f.relative_to(base_path))
                for f in base_path.glob(pattern)
                if f.is_file()
            ]
        except Exception as e:
            logger.error(f"❌ 本地列表失败: {e}")
            return []

    # ==================== 工具方法 ====================

    def _get_extension(self, content_type: str) -> str:
        """根据Content-Type获取文件扩展名"""
        mapping = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "application/pdf": ".pdf",
            "text/plain": ".txt",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"
        }
        return mapping.get(content_type, ".bin")


# ==================== 全局实例 ====================

_minio_service: Optional[MinIOService] = None


def get_minio_service() -> MinIOService:
    """
    获取MinIO服务单例

    Returns:
        MinIOService实例
    """
    global _minio_service
    if _minio_service is None:
        _minio_service = MinIOService()
    return _minio_service


# ==================== 使用示例 ====================

"""
from app.services.storage import get_minio_service

service = get_minio_service()

# 上传商品图片
with open("product.jpg", "rb") as f:
    url = await service.upload_product_image(
        product_id=1,
        image_data=f.read(),
        content_type="image/jpeg"
    )
    print(f"图片URL: {url}")

# 下载商品图片
data = await service.download_product_image(product_id=1)

# 删除商品图片
await service.delete_product_image(product_id=1)

# 获取预签名URL
url = service.get_presigned_url(
    bucket="products",
    object_name="products/1.jpg",
    expires=timedelta(hours=24)
)
"""
