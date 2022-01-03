import igrf_model
import os

datas = [
    (
        os.path.join(os.path.dirname(igrf_model.__file__), "data"),
        "igrf_model/data",
    )
]
