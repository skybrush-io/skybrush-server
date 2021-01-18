import lark
import os

datas = [
    (
        os.path.join(os.path.dirname(lark.__file__), "grammars", "*.lark"),
        "lark/grammars"
    )
]
