# file_edit — Examples

## 1. Single-line replacement (success)

**Input**
```json
{ "path": "~/project/config.py", "new_content": "DEBUG = False\n", "start_line": 5, "end_line": 5 }
```
**Output**
```json
{ "success": true, "result": { "path": "/home/user/project/config.py", "lines_changed": 1, "backup_path": null } }
```

## 2. Full file replacement (success)

**Input**
```json
{ "path": "~/project/hello.py", "new_content": "print('hello')\n" }
```
**Output**
```json
{ "success": true, "result": { "path": "/home/user/project/hello.py", "lines_changed": 1, "backup_path": null } }
```

## 3. Binary file rejected (failure)

**Input**
```json
{ "path": "~/photo.png", "new_content": "..." }
```
**Output**
```json
{ "success": false, "error": "BINARY_FILE", "message": "Binary files cannot be edited as text" }
```

## 4. File not found (failure)

**Input**
```json
{ "path": "~/nonexistent.py", "new_content": "x = 1\n", "start_line": 1 }
```
**Output**
```json
{ "success": false, "error": "FILE_NOT_FOUND", "message": "No such file: /home/user/nonexistent.py" }
```
