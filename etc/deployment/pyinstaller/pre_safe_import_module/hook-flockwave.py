import pkgutil


def pre_safe_import_module(api):
    # Discover all folders in the Python path where the "flockwave" package
    # exists because apparently PyInstaller does not recognize that it is
    # a namespace package
    paths = pkgutil.extend_path([], api.module_name)
    for path in paths:
        api.append_package_path(path)
