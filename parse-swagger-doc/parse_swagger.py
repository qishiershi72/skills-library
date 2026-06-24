#!/usr/bin/env python3
"""
Swagger/OpenAPI 文档解析脚本

依据 SKILL.md 规范实现：
- 从 swagger-resources 抓取分组列表（携带 Cookie）
- 依次抓取各分组 api-docs（兼容 Swagger2 / OpenAPI3）
- 递归展开 $ref / allOf / oneOf / anyOf，最终 schema 不含 $ref
- 输出统一结构 JSON

用法:
    python parse_swagger.py <swaggerResourcesUrl> "<cookie>"
    python parse_swagger.py https://test.xx.com/swagger-resources "JSESSIONID=xxx; token=xxx"

仅向 stdout 输出合法 JSON。
"""

import json
import sys
from collections import OrderedDict
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_get_json(url, cookie, timeout=30):
    """GET 请求并解析为 JSON。失败抛出异常。"""
    headers = {"Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


# --------------------------------------------------------------------------
# URL 拼接
# --------------------------------------------------------------------------
def resolve_location(base_url, location):
    """若 location 为相对路径，拼接 base_url 的协议+域名。"""
    if location.startswith("http://") or location.startswith("https://"):
        return location
    parsed = urlparse(base_url)
    origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return urljoin(origin + "/", location.lstrip("/"))


# --------------------------------------------------------------------------
# Schema 展开（去除所有 $ref）
# --------------------------------------------------------------------------
def get_ref(ref, root):
    """解析 $ref，例如 #/definitions/User 或 #/components/schemas/User。"""
    if not ref.startswith("#/"):
        return {}
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return {}
    return node


def merge_objects(target, source):
    """合并 allOf 中的对象（properties / required）。"""
    if "properties" in source:
        target.setdefault("properties", OrderedDict())
        for k, v in source["properties"].items():
            target["properties"][k] = v
    if "required" in source:
        target.setdefault("required", [])
        for r in source["required"]:
            if r not in target["required"]:
                target["required"].append(r)
    for k, v in source.items():
        if k not in ("properties", "required", "allOf", "oneOf", "anyOf", "$ref"):
            target.setdefault(k, v)


def expand_schema(schema, root, seen=None):
    """
    递归展开 schema，返回简化类型树：
      - 对象 -> OrderedDict{field: 子类型}
      - 数组 -> [元素类型]
      - 基础类型 -> 类型字符串 ("string"/"integer"/"number"/"boolean")
    最终结果不包含任何 $ref。
    """
    if seen is None:
        seen = set()
    if not isinstance(schema, dict):
        return "string"

    # $ref
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            return {}  # 防止循环引用
        seen = seen | {ref}
        return expand_schema(get_ref(ref, root), root, seen)

    # allOf：合并
    if "allOf" in schema:
        merged = OrderedDict()
        for sub in schema["allOf"]:
            resolved = get_ref(sub["$ref"], root) if "$ref" in sub else sub
            merge_objects(merged, resolved)
        for k, v in schema.items():
            if k != "allOf":
                merge_objects(merged, {k: v}) if k in ("properties", "required") else merged.setdefault(k, v)
        return expand_schema(merged, root, seen)

    # oneOf / anyOf：取第一个
    if "oneOf" in schema and schema["oneOf"]:
        return expand_schema(schema["oneOf"][0], root, seen)
    if "anyOf" in schema and schema["anyOf"]:
        return expand_schema(schema["anyOf"][0], root, seen)

    stype = schema.get("type")

    # 数组
    if stype == "array" or "items" in schema:
        items = schema.get("items", {})
        return [expand_schema(items, root, seen)]

    # 对象
    if stype == "object" or "properties" in schema:
        props = schema.get("properties", {})
        result = OrderedDict()
        for name, prop in props.items():
            result[name] = expand_schema(prop, root, seen)
        # additionalProperties 视为 Map
        if not props and isinstance(schema.get("additionalProperties"), dict):
            result["{key}"] = expand_schema(schema["additionalProperties"], root, seen)
        return result

    # 基础类型
    if stype in ("string", "integer", "number", "boolean"):
        return stype
    return "string"


# --------------------------------------------------------------------------
# Example 生成
# --------------------------------------------------------------------------
def gen_example(simplified):
    """根据简化类型树生成示例值。"""
    if isinstance(simplified, OrderedDict) or isinstance(simplified, dict):
        return OrderedDict((k, gen_example(v)) for k, v in simplified.items())
    if isinstance(simplified, list):
        inner = simplified[0] if simplified else "string"
        return [gen_example(inner)]
    mapping = {"string": "", "integer": 0, "number": 0, "boolean": False}
    return mapping.get(simplified, "")


def pick_example(node, simplified):
    """优先使用定义的 example，否则按 schema 生成。"""
    if isinstance(node, dict):
        if "example" in node:
            return node["example"]
        if "examples" in node and node["examples"]:
            ex = node["examples"]
            if isinstance(ex, dict):
                first = next(iter(ex.values()))
                if isinstance(first, dict) and "value" in first:
                    return first["value"]
                return first
            if isinstance(ex, list):
                return ex[0]
        if isinstance(node.get("schema"), dict) and "example" in node["schema"]:
            return node["schema"]["example"]
    return gen_example(simplified)


# --------------------------------------------------------------------------
# ContentType
# --------------------------------------------------------------------------
KNOWN_CONTENT_TYPES = (
    "application/json",
    "multipart/form-data",
    "application/x-www-form-urlencoded",
    "application/octet-stream",
    "text/plain",
)


def normalize_content_type(ct):
    if not ct:
        return "application/json"
    ct = ct.split(";")[0].strip().lower()
    return ct if ct in KNOWN_CONTENT_TYPES else "application/json"


# --------------------------------------------------------------------------
# 参数统一格式
# --------------------------------------------------------------------------
def unify_param(p, root):
    schema = p.get("schema", {})
    ptype = p.get("type") or (schema.get("type") if isinstance(schema, dict) else None) or "string"
    return OrderedDict([
        ("name", p.get("name", "")),
        ("type", ptype),
        ("required", bool(p.get("required", False))),
        ("description", p.get("description", "")),
        ("example", p.get("example", schema.get("example") if isinstance(schema, dict) else None)),
        ("default", p.get("default", schema.get("default") if isinstance(schema, dict) else None)),
        ("enum", p.get("enum", schema.get("enum") if isinstance(schema, dict) else None)),
    ])


# --------------------------------------------------------------------------
# 单个接口解析
# --------------------------------------------------------------------------
def parse_operation(group, path, method, op, root, doc_consumes, doc_produces, base_path=""):
    parameters = OrderedDict([
        ("path", []),
        ("query", []),
        ("header", []),
        ("formData", []),
        ("body", None),
    ])

    consumes = op.get("consumes", doc_consumes) or []
    produces = op.get("produces", doc_produces) or []

    # Swagger2 parameters
    for p in op.get("parameters", []):
        loc = p.get("in")
        if loc in ("path", "query", "header", "formData"):
            parameters[loc].append(unify_param(p, root))
        elif loc == "body":
            schema = p.get("schema", {})
            simplified = expand_schema(schema, root)
            parameters["body"] = OrderedDict([
                ("required", bool(p.get("required", False))),
                ("schema", simplified),
                ("example", pick_example(schema, simplified)),
            ])

    # OpenAPI3 requestBody
    if "requestBody" in op:
        rb = op["requestBody"]
        content = rb.get("content", {})
        ct = next(iter(content), None)
        media = content.get(ct, {}) if ct else {}
        schema = media.get("schema", {})
        simplified = expand_schema(schema, root)
        parameters["body"] = OrderedDict([
            ("required", bool(rb.get("required", False))),
            ("schema", simplified),
            ("example", pick_example(media, simplified)),
        ])
        if ct and not consumes:
            consumes = [ct]

    # contentType
    content_type = normalize_content_type(consumes[0] if consumes else (produces[0] if produces else None))

    # response：第一个 2xx，否则 default / 第一个
    responses = op.get("responses", {}) or {}
    resp_key = None
    for code in responses:
        if str(code).startswith("2"):
            resp_key = code
            break
    if resp_key is None:
        resp_key = "default" if "default" in responses else (next(iter(responses), None))

    response = OrderedDict([
        ("statusCode", 200),
        ("contentType", "application/json"),
        ("schema", {}),
        ("example", {}),
    ])
    if resp_key is not None:
        try:
            response["statusCode"] = int(resp_key)
        except (ValueError, TypeError):
            response["statusCode"] = 200
        resp = responses.get(resp_key, {})
        # OpenAPI3 content
        if "content" in resp:
            content = resp["content"]
            ct = next(iter(content), None)
            media = content.get(ct, {}) if ct else {}
            schema = media.get("schema", {})
            simplified = expand_schema(schema, root) if schema else {}
            response["contentType"] = normalize_content_type(ct)
            response["schema"] = simplified
            response["example"] = pick_example(media, simplified)
        # Swagger2 schema
        elif "schema" in resp:
            schema = resp["schema"]
            simplified = expand_schema(schema, root)
            response["contentType"] = normalize_content_type(produces[0] if produces else None)
            response["schema"] = simplified
            response["example"] = pick_example(resp, simplified)
        else:
            response["contentType"] = normalize_content_type(produces[0] if produces else None)
            if "example" in resp:
                response["example"] = resp["example"]

    return OrderedDict([
        ("group", group),
        ("tags", op.get("tags", [])),
        ("summary", op.get("summary", "")),
        ("description", op.get("description", "")),
        ("operationId", op.get("operationId", "")),
        ("deprecated", bool(op.get("deprecated", False))),
        ("method", method.upper()),
        ("path", join_path(base_path, path)),
        ("contentType", content_type),
        ("consumes", consumes),
        ("produces", produces),
        ("parameters", parameters),
        ("response", response),
    ])


HTTP_METHODS = ("get", "post", "put", "delete", "patch", "options", "head")


def join_path(base_path, path):
    """拼接 basePath 与 path，规范化斜杠。"""
    base = (base_path or "").rstrip("/")
    if not base:
        return path
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def extract_base_path(doc):
    """
    提取路径前缀：
      - Swagger2: basePath
      - OpenAPI3: servers[0].url 的 path 部分
    """
    if doc.get("basePath"):
        return doc["basePath"]
    servers = doc.get("servers")
    if isinstance(servers, list) and servers:
        url = servers[0].get("url", "") if isinstance(servers[0], dict) else ""
        if url:
            parsed = urlparse(url)
            return parsed.path if parsed.scheme else url
    return ""


def parse_doc(group, doc):
    """解析单个 api-docs 文档，返回 api 列表。"""
    apis = []
    doc_consumes = doc.get("consumes", [])
    doc_produces = doc.get("produces", [])
    base_path = extract_base_path(doc)
    paths = doc.get("paths", {}) or {}
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            apis.append(parse_operation(group, path, method, op, doc, doc_consumes, doc_produces, base_path))
    return apis


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def parse_swagger(swagger_resources_url, cookie):
    # 第一步：swagger-resources
    try:
        resources = http_get_json(swagger_resources_url, cookie)
    except (HTTPError, URLError, ValueError, OSError) as e:
        return OrderedDict([
            ("success", False),
            ("url", swagger_resources_url),
            ("message", str(e)),
        ])

    all_apis = []
    errors = []

    for item in resources:
        group = item.get("name", "")
        location = item.get("location", "")
        url = resolve_location(swagger_resources_url, location)
        try:
            doc = http_get_json(url, cookie)
            all_apis.extend(parse_doc(group, doc))
        except (HTTPError, URLError, ValueError, OSError) as e:
            errors.append(OrderedDict([("url", url), ("message", str(e))]))

    result = OrderedDict([("success", True), ("apis", all_apis)])
    if errors:
        result["errors"] = errors
    return result


def main():
    if len(sys.argv) < 2:
        sys.stderr.write('用法: python parse_swagger.py <swaggerResourcesUrl> "<cookie>"\n')
        sys.exit(1)
    swagger_resources_url = sys.argv[1]
    cookie = sys.argv[2] if len(sys.argv) > 2 else ""
    result = parse_swagger(swagger_resources_url, cookie)
    # 仅输出合法 JSON
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
