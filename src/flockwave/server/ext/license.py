import json

__all__ = ("load", )


def load(app, configuration, logger):
    try:
        from pytransform import get_license_info, get_expired_days
        show_license_information(logger, get_license_info(), get_expired_days())
    except ImportError:
        # licensing not used
        pass
        
        
def show_license_information(logger, info, days_left):
    data = info.get("DATA")
    if data:
        try:
            data = json.loads(data)
        except Exception:
            data = None
    if data:
        licensee = data.get("licensee")
        if licensee:
            logger.info(f"Licensed to {licensee}")
    if days_left >= 15:
        logger.info(f"This license expires in {days_left} days")
    elif days_left > 1:
        logger.warn(f"This license expires in {days_left} days")
    elif days_left == 1:
        logger.warn(f"This license expires in one day")
    elif days_left == 0:
        logger.warn(f"This license expires today")
