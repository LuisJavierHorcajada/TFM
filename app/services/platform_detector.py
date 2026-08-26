"""
ESI-Bench - Platform / Cloud Provider Auto-Detector.

Detects cloud provider (AWS, Azure, OpenStack/Kolla) and instance metadata
via Instance Metadata Services (IMDS) with DMI/system fallbacks.
"""

import json
import logging
import os
import urllib.error
import urllib.request

from app.models.schemas import PlatformInfo

logger = logging.getLogger("esi_bench.platform")

IMDS_TIMEOUT_SECONDS = 1.0


def _fetch_url(
    url: str,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    data: bytes | None = None,
) -> str | None:
    """Fetch URL with a short timeout. Returns string body or None on failure."""
    req = urllib.request.Request(url, headers=headers or {}, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=IMDS_TIMEOUT_SECONDS) as resp:
            if resp.status == 200:
                return resp.read().decode("utf-8", errors="replace")
    except Exception:
        pass
    return None


def _check_openstack() -> PlatformInfo | None:
    """
    Check OpenStack native metadata service.
    OpenStack provides http://169.254.169.254/openstack/latest/meta_data.json
    which is unique to OpenStack (AWS and Azure do not have this path).
    """
    resp = _fetch_url("http://169.254.169.254/openstack/latest/meta_data.json")
    if not resp:
        resp = _fetch_url("http://169.254.169.254/openstack/2018-08-27/meta_data.json")
    if not resp:
        # Check if the /openstack/ root directory exists
        resp_root = _fetch_url("http://169.254.169.254/openstack/")
        if not resp_root:
            return None

    try:
        data = json.loads(resp) if resp else {}
        instance_id = data.get("uuid")
        instance_type = (
            data.get("instance_type")
            or data.get("meta", {}).get("flavor")
            or data.get("meta", {}).get("instance_type")
        )

        # If flavor not in JSON, get it from the EC2 compatibility layer
        if not instance_type:
            instance_type = _fetch_url("http://169.254.169.254/latest/meta-data/instance-type")

        az = data.get("availability_zone")
        if not az:
            az = _fetch_url("http://169.254.169.254/latest/meta-data/placement/availability-zone")

        hostname = data.get("hostname") or data.get("name")

        return PlatformInfo(
            provider="openstack",
            instance_type=str(instance_type).strip() if instance_type else "custom",
            region=str(az).strip() if az else None,
            instance_id=instance_id,
            details={"hostname": hostname, "project_id": data.get("project_id")},
        )
    except Exception as e:
        logger.debug("Failed to parse OpenStack metadata: %s", e)
        return PlatformInfo(provider="openstack")


def _check_azure() -> PlatformInfo | None:
    """Check Azure Instance Metadata Service."""
    headers = {"Metadata": "true"}
    resp = _fetch_url(
        "http://169.254.169.254/metadata/instance?api-version=2021-02-01",
        headers=headers,
    )
    if not resp:
        return None

    try:
        data = json.loads(resp)
        compute = data.get("compute", {})
        return PlatformInfo(
            provider="azure",
            instance_type=compute.get("vmSize"),
            region=compute.get("location"),
            instance_id=compute.get("vmId"),
            details={
                "os_type": compute.get("osType"),
                "sku": compute.get("sku"),
                "resource_group": compute.get("resourceGroupName"),
            },
        )
    except Exception as e:
        logger.debug("Failed to parse Azure metadata: %s", e)
        return None


def _check_aws() -> PlatformInfo | None:
    """
    Check genuine AWS IMDS (supports both IMDSv2 and IMDSv1).
    Requests IMDSv2 session token first, then queries the AWS dynamic
    instance identity document and metadata.
    """
    # 1. Request IMDSv2 session token (TTL: 60s)
    token_headers = {"X-aws-ec2-metadata-token-ttl-seconds": "60"}
    token = _fetch_url(
        "http://169.254.169.254/latest/api/token",
        headers=token_headers,
        method="PUT",
    )
    headers = {"X-aws-ec2-metadata-token": token} if token else {}

    # 2. Try AWS dynamic instance identity document (only exists on authentic AWS)
    identity_doc = _fetch_url(
        "http://169.254.169.254/latest/dynamic/instance-identity/document",
        headers=headers,
    )
    if identity_doc:
        try:
            data = json.loads(identity_doc)
            if "accountId" in data and "instanceType" in data:
                return PlatformInfo(
                    provider="aws",
                    instance_type=data.get("instanceType"),
                    region=data.get("region"),
                    instance_id=data.get("instanceId"),
                    details={
                        "availability_zone": data.get("availabilityZone"),
                        "account_id": data.get("accountId"),
                        "architecture": data.get("architecture"),
                        "imds_version": "v2" if token else "v1",
                    },
                )
        except Exception:
            pass

    # 3. Fallback: Query metadata endpoints directly with headers
    instance_type = _fetch_url(
        "http://169.254.169.254/latest/meta-data/instance-type", headers=headers
    )
    if not instance_type:
        return None

    # Check DMI to verify we are not in OpenStack / QEMU / KVM emulating EC2 metadata
    vendor = ""
    for path in [
        "/sys/class/dmi/id/sys_vendor",
        "/sys/class/dmi/id/board_vendor",
        "/sys/class/dmi/id/product_name",
    ]:
        try:
            if os.path.exists(path):
                with open(path, "r", errors="ignore") as f:
                    vendor += f.read().lower()
        except Exception:
            pass

    if "openstack" in vendor or "kolla" in vendor or "bochs" in vendor:
        return None

    instance_id = _fetch_url(
        "http://169.254.169.254/latest/meta-data/instance-id", headers=headers
    )
    az = _fetch_url(
        "http://169.254.169.254/latest/meta-data/placement/availability-zone", headers=headers
    )
    region = az[:-1] if az and len(az) > 1 else az

    return PlatformInfo(
        provider="aws",
        instance_type=instance_type.strip(),
        region=region.strip() if region else None,
        instance_id=instance_id.strip() if instance_id else None,
        details={
            "availability_zone": az.strip() if az else None,
            "imds_version": "v2" if token else "v1",
        },
    )


def _check_dmi_and_env() -> PlatformInfo:
    """Fallback: check environment variables and DMI /sys files."""
    env_provider = os.getenv("PLATFORM_PROVIDER")
    env_instance = os.getenv("PLATFORM_INSTANCE_TYPE")
    env_region = os.getenv("PLATFORM_REGION")
    if env_provider:
        return PlatformInfo(
            provider=env_provider.lower(),
            instance_type=env_instance,
            region=env_region,
            details={"source": "environment_variables"},
        )

    vendor = ""
    product = ""
    for path, var in [
        ("/sys/class/dmi/id/sys_vendor", "vendor"),
        ("/sys/class/dmi/id/product_name", "product"),
    ]:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    val = f.read().strip()
                    if var == "vendor":
                        vendor = val
                    else:
                        product = val
        except Exception:
            pass

    combined = f"{vendor} {product}".lower()
    if "openstack" in combined or "kolla" in combined:
        return PlatformInfo(provider="openstack", details={"dmi_vendor": vendor, "dmi_product": product})
    if "amazon" in combined or "ec2" in combined:
        return PlatformInfo(provider="aws", details={"dmi_vendor": vendor, "dmi_product": product})
    if "microsoft" in combined or "azure" in combined:
        return PlatformInfo(provider="azure", details={"dmi_vendor": vendor, "dmi_product": product})
    if "kvm" in combined or "qemu" in combined:
        return PlatformInfo(provider="kvm/qemu", details={"dmi_vendor": vendor, "dmi_product": product})

    return PlatformInfo(
        provider="local/baremetal" if not vendor else f"other ({vendor})",
        details={"dmi_vendor": vendor, "dmi_product": product} if vendor else None,
    )


def detect_platform() -> PlatformInfo:
    """
    Run detection strategies in priority order:
    1. Explicit Environment Override (Instant, ideal for FIWARE and local testing)
    2. OpenStack IMDS (native openstack/ metadata check)
    3. Azure IMDS
    4. AWS IMDS (IMDSv2 + identity document)
    5. DMI Hardware fallback
    """
    # 1. Comprobación prioritaria por variable de entorno
    env_provider = os.getenv("PLATFORM_PROVIDER") or os.getenv("PLATFORM_NAME")
    if env_provider:
        env_instance = os.getenv("PLATFORM_INSTANCE_TYPE") or os.getenv("INSTANCE_TYPE", "custom")
        env_region = os.getenv("PLATFORM_REGION") or os.getenv("REGION", "local")
        logger.info("Detected platform (env override): %s", env_provider.lower())
        return PlatformInfo(
            provider=env_provider.lower(),
            instance_type=env_instance,
            region=env_region,
            details={"source": "environment_variables"},
        )

    # 2. Detección OpenStack
    try:
        os_info = _check_openstack()
        if os_info:
            logger.info("Detected platform: OpenStack (%s, %s)", os_info.instance_type, os_info.region)
            return os_info
    except Exception as e:
        logger.debug("OpenStack detection error: %s", e)

    # 3. Detección Azure
    try:
        azure_info = _check_azure()
        if azure_info:
            logger.info("Detected platform: Azure (%s, %s)", azure_info.instance_type, azure_info.region)
            return azure_info
    except Exception as e:
        logger.debug("Azure detection error: %s", e)

    # 4. Detección AWS
    try:
        aws_info = _check_aws()
        if aws_info:
            logger.info("Detected platform: AWS (%s, %s)", aws_info.instance_type, aws_info.region)
            return aws_info
    except Exception as e:
        logger.debug("AWS detection error: %s", e)

    # 5. DMI Hardware / Sys fallback
    fallback = _check_dmi_and_env()
    logger.info("Detected platform (fallback): %s", fallback.provider)
    return fallback
