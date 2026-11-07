import logging
import os
try:
    from config import settings
except ImportError:
    from ..config import settings
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)
logging.getLogger("qcloud_cos.cos_client").setLevel(logging.WARNING)
logging.getLogger("qcloud_cos.cos_auth").setLevel(logging.WARNING)

class COSService:
    def __init__(self):
        self.secret_id = settings.tencent_cos_secret_id
        self.secret_key = settings.tencent_cos_secret_key
        self.region = settings.tencent_cos_region
        self.bucket = settings.tencent_cos_bucket
        self.client = None
        self.init_error = None

        if self.secret_id and self.secret_key and self.region and self.bucket:
            try:
                from qcloud_cos import CosConfig, CosS3Client

                config = CosConfig(Region=self.region, SecretId=self.secret_id, SecretKey=self.secret_key)
                self.client = CosS3Client(config)
                logger.info(f"Tencent COS Client initialized for bucket: {self.bucket}")
            except Exception as e:
                self.init_error = str(e)
                logger.error(f"Failed to initialize Tencent COS Client: {e}")
        else:
            self.init_error = "Missing COS credentials or bucket/region configuration"
            logger.warning("Tencent COS credentials not found. COS upload will be disabled.")

    def upload_file_verbose(self, local_path: str, key: str) -> tuple[str | None, str | None]:
        """
        Upload file and return (url, error_message) for diagnostics.
        """
        if not self.client:
            reason = self.init_error or "COS client not initialized"
            logger.info(f"COS client not initialized, skipping upload. reason={reason}")
            return None, reason

        if not os.path.exists(local_path):
            reason = f"Local file not found: {local_path}"
            logger.error(reason)
            return None, reason

        try:
            self.client.upload_file(
                Bucket=self.bucket,
                LocalFilePath=local_path,
                Key=key,
                EnableMD5=False
            )
            url = f"https://{self.bucket}.cos.{self.region}.myqcloud.com/{key}"
            logger.info(f"Successfully uploaded to COS: {url}")
            return url, None
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            logger.error(f"Failed to upload file to COS: {reason}")
            return None, reason

    def upload_file(self, local_path: str, key: str) -> str:
        """
        Upload a file to Tencent COS.
        
        Args:
            local_path: Absolute path to the local file.
            key: Object key (path) in the bucket.
            
        Returns:
            str: The full URL of the uploaded file, or None if upload failed/disabled.
        """
        url, _error = self.upload_file_verbose(local_path, key)
        return url
    
    def file_exists(self, key: str) -> bool:
        """
        Check if a file exists in COS.
        
        Args:
            key: Object key (path) in the bucket.
            
        Returns:
            bool: True if file exists, False otherwise.
        """
        if not self.client:
            return False
        
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False
    
    def get_file_url(self, key: str) -> str:
        """
        Construct the full URL for a COS object.
        
        Args:
            key: Object key (path) in the bucket.
            
        Returns:
            str: Full URL to the object.
        """
        return f"https://{self.bucket}.cos.{self.region}.myqcloud.com/{key}"
    
    def list_files_with_prefix(self, prefix: str, max_keys: int = 100) -> list:
        """
        List files in COS with a given prefix.
        
        Args:
            prefix: Prefix to filter objects.
            max_keys: Maximum number of keys to return.
            
        Returns:
            list: List of object keys matching the prefix.
        """
        if not self.client:
            return []
        
        try:
            response = self.client.list_objects(
                Bucket=self.bucket,
                Prefix=prefix,
                MaxKeys=max_keys
            )
            if 'Contents' in response:
                return [obj['Key'] for obj in response['Contents']]
            return []
        except Exception as e:
            logger.error(f"Failed to list COS files: {e}")
            return []

    def download_file(self, key: str, local_path: str) -> bool:
        """
        Download a file from Tencent COS to local path.
        
        Args:
            key: Object key (path) in the bucket.
            local_path: Absolute path to save the file.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        if not self.client:
            return False
            
        try:
            self.client.download_file(
                Bucket=self.bucket,
                Key=key,
                DestFilePath=local_path
            )
            return True
        except Exception as e:
            logger.error(f"Failed to download file from COS: {e}")
            return False

    def get_presigned_download_url(self, key: str, expires: int = 3600) -> str | None:
        """Generate a time-limited presigned URL for private object access.

        Args:
            key: Object key in the bucket.
            expires: URL validity in seconds (default 1 hour).

        Returns:
            Presigned URL string, or None if COS client is unavailable.
        """
        if not self.client:
            return None
        try:
            url = self.client.get_presigned_download_url(
                Bucket=self.bucket,
                Key=key,
                Expired=expires,
            )
            return url
        except Exception as e:
            logger.error(f"Failed to generate presigned URL for {key}: {e}")
            return None

    def delete_file(self, key: str) -> bool:
        """
        Delete a file from Tencent COS.

        Args:
            key: Object key (path) in the bucket.

        Returns:
            bool: True if successful, False otherwise.
        """
        if not self.client:
            return False

        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            logger.info(f"Successfully deleted COS object: {key}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete COS object: {e}")
            return False
