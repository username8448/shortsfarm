# pyproject.toml: зависимости и package-data

Добавь runtime-зависимости:

```toml
fastapi
uvicorn[standard]
jinja2
```

И проверь, что package data включает web-файлы. Вариант для setuptools:

```toml
[tool.setuptools.package-data]
shortfarm = [
  "web/templates/*.html",
  "web/static/*.css",
  "web/static/*.js",
]
```
