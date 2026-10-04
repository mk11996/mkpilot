# API 接口设计

## 认证约定

设备 REST 请求：

```http
Authorization: JWT <device-jwt>
```

App 请求：

```http
Authorization: Bearer <app-access-token>
```

## 设备接口

```text
POST /api/v1/enroll
GET  /api/v1/devices/{device_id}
POST /api/v1/devices/{device_id}/heartbeat
POST /api/v1/devices/{device_id}/revoke
```

## 原生上传兼容接口

```text
GET /v1.4/{device_id}/upload_url/?path=<relative_path>
PUT /storage/put/<temporary_token>
```

## App 接口

```text
POST /api/v1/app/login
GET  /api/v1/app/me
GET  /api/v1/devices
GET  /api/v1/devices/{device_id}/status
GET  /api/v1/devices/{device_id}/routes
GET  /api/v1/devices/{device_id}/files
GET  /api/v1/files/{file_id}/download
POST /api/v1/devices/{device_id}/commands/{command}
```

## 错误码

```text
400 请求格式错误
401 认证失败
403 权限不足或设备已撤销
404 资源不存在
409 文件已存在或注册码已使用
413 文件过大
429 请求过于频繁
500 服务器内部错误
```

## 上传响应

成功：

```json
{
  "file_id": "uuid",
  "path": "2026-08-04--12-30-00/0/qlog.bz2",
  "size_bytes": 123456,
  "sha256": "...",
  "uploaded_at": "2026-08-04T12:30:00Z"
}
```

