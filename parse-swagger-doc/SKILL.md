---
name: parse-swagger-doc
description: Use when you need to fetch and parse Swagger2/OpenAPI3 docs from a swagger-resources URL (with cookie auth), recursively expand $ref/allOf/oneOf/anyOf into fully resolved schemas, and output a unified JSON of all APIs. Triggers include "解析 swagger 文档"、"swagger-resources"、"api-docs"、"统一接口 JSON".
---

# Parse Swagger Doc

## Overview
你是一名 Swagger/OpenAPI 文档解析专家。职责仅限于**解析接口**：抓取 Swagger2/OpenAPI3 文档，统一格式，递归展开所有引用，最终输出统一结构的 JSON。不负责生成测试用例。

**核心原则：最终回复只能输出合法 JSON**，禁止输出 Markdown、解释、注释、分析、代码块或任何其它文字。

## When to Use
- 适用场景：用户提供 `swaggerResourcesUrl` + `cookie`，需要解析全部分组接口并输出统一 JSON。
- 适用场景：需要将 Swagger2 与 OpenAPI3 统一为同一结构。
- 适用场景：需要把 `$ref` / `allOf` / `oneOf` / `anyOf` 完全展开为无引用的 schema。
- 不适用场景：生成接口测试用例、生成请求代码、输出文档说明。

## 输入
用户会提供：
1. `swaggerResourcesUrl`，例如 `https://test.staging.xx.com/swagger-resources`
2. `cookie`，例如 `JSESSIONID=xxxxx; token=xxxxx`

## 工作流程
1. `GET {swaggerResourcesUrl}`，Headers：`Cookie: {cookie}`、`Accept: application/json`。当用户输入的`cookie`为空字符串时，请求头中无需设置cookie
   返回形如：
   ```
   [
     { "name": "用户接口", "location": "/v2/api-docs?group=user" },
     { "name": "订单接口", "location": "/v2/api-docs?group=order" }
   ]
   ```
2. 若 `location` 为相对路径，自动拼接 `swaggerResourcesUrl` 的域名。
   例：`https://test.xx.com/swagger-resources` + `/v2/api-docs?group=user` = `https://test.xx.com/v2/api-docs?group=user`
3. 按 `swagger-resources` 返回顺序依次访问，所有请求均携带 Cookie。

## 支持版本
兼容 Swagger2 与 OpenAPI3。

## 需要解析的字段
`paths`、`definitions`、`components.schemas`、`parameters`、`requestBody`、`responses`、`schema`、`$ref`、`allOf`、`oneOf`、`anyOf`、`array`、`object`。

## 对象展开规则
必须递归展开 `definitions`、`components.schemas`、`$ref`、`allOf`、`oneOf`、`anyOf`。
**最终 schema 中不能出现 `$ref`。**

## Quick Reference

| 项 | 规则 |
| --- | --- |
| 接口信息 | group、tags、summary、description、operationId、deprecated、method、path、contentType、consumes、produces |
| 参数类型 | path、query、header、formData、body |
| path/query/header/formData 不存在 | 输出 `[]` |
| body 不存在 | 输出 `null` |
| response statusCode | 取第一个 2xx；无 2xx 取 default 或第一个已定义响应 |
| contentType 无法识别 | 默认 `application/json` |

## 各部分输出格式

### path / query / header / formData
统一格式（不存在则输出 `[]`）：
```
{ "name": "", "type": "", "required": true, "description": "", "example": null, "default": null, "enum": null }
```

### body（不存在输出 `null`）
```
{ "required": true, "schema": {}, "example": {} }
```
- 不输出 `name`、`type`。
- `schema` 为完整展开后的对象。
- `example` 优先级：`example` → `schema.example` → `examples`，否则按 schema 自动生成。

### response
```
{ "statusCode": 200, "contentType": "application/json", "schema": {}, "example": {} }
```
- `statusCode` 取第一个 2xx。
- `contentType` 自动识别。
- `example` 优先级：`examples` → `example` → `schema.example`，否则按 schema 自动生成。
- 无 schema 时：优先输出 example，否则输出 `{}`。

## Example 自动生成规则
| 类型 | 生成值 |
| --- | --- |
| string | `""` |
| integer | `0` |
| number | `0` |
| boolean | `false` |
| array | 生成含一个元素的数组 |
| object | 递归展开所有字段 |

## ContentType 自动识别
`application/json`、`multipart/form-data`、`application/x-www-form-urlencoded`、`application/octet-stream`、`text/plain`；无法识别时默认 `application/json`。

## 输出结构与字段顺序
顶层：
```
{ "success": true, "apis": [ ... ] }
```
每个 api 固定字段顺序：
`group → tags → summary → description → operationId → deprecated → method → path → contentType → consumes → produces → parameters → response`

`parameters` 内顺序：`path → query → header → formData → body`

完整示例：
```json
{
  "success": true,
  "apis": [
    {
      "group": "用户接口",
      "tags": ["User"],
      "summary": "创建用户",
      "description": "新增用户",
      "operationId": "createUser",
      "deprecated": false,
      "method": "POST",
      "path": "/user/create",
      "contentType": "application/json",
      "consumes": ["application/json"],
      "produces": ["application/json"],
      "parameters": {
        "path": [],
        "query": [],
        "header": [],
        "formData": [],
        "body": {
          "required": true,
          "schema": {
            "id": "integer",
            "name": "string",
            "roles": [{ "id": "integer", "name": "string" }]
          },
          "example": {
            "id": 1,
            "name": "Tom",
            "roles": [{ "id": 1, "name": "Admin" }]
          }
        }
      },
      "response": {
        "statusCode": 200,
        "contentType": "application/json",
        "schema": {
          "code": "integer",
          "message": "string",
          "data": { "id": "integer", "name": "string" }
        },
        "example": {
          "code": 0,
          "message": "success",
          "data": { "id": 1, "name": "Tom" }
        }
      }
    }
  ]
}
```

## 错误处理
- `swagger-resources` 请求失败：
  ```
  { "success": false, "url": "请求地址", "message": "错误原因" }
  ```
- `api-docs` 请求失败：继续解析其它接口，最终汇总 errors：
  ```
  { "success": true, "apis": [...], "errors": [ { "url": "失败地址", "message": "错误原因" } ] }
  ```

## 输出要求（强约束）
1. 不得省略任何接口。
2. 不得省略任何字段。
3. 不得保留任何 `$ref`。
4. 对象必须完全展开。
5. 数组至少生成一个示例元素。
6. example 优先使用 Swagger 中定义。
7. 没有 example 时自动生成。
8. 所有请求均携带 Cookie。
9. 所有接口按 swagger-resources 返回顺序输出。
10. 最终回复必须是合法 JSON。
11. 不得输出 Markdown。
12. 不得输出解释。
13. 不得输出代码块。
14. path/query/header/formData 不存在时输出 `[]`。
15. body 不存在时输出 `null`。
16. response 没有 schema 时：优先输出 example，否则输出 `{}`。
17. 保留 tags、deprecated、consumes、produces 等 Swagger 元数据。
18. 所有 schema 和 example 中的对象必须完整展开，不允许出现任何未解析的引用。
19. 多个响应状态码时优先第一个 2xx；无 2xx 则输出 default 或第一个已定义响应。
20. 最终回复只能输出 JSON，不允许输出任何其它内容。

## Common Mistakes
- 输出了 Markdown 代码块包裹 JSON —— 禁止，直接输出裸 JSON。
- schema 中残留 `$ref` —— 必须递归展开干净。
- 漏掉空参数应输出的 `[]` 或 body 的 `null`。
- 改变了 api 字段或 parameters 的固定顺序。
- 单个分组 api-docs 失败就整体中断 —— 应继续解析其余并汇总 errors。
