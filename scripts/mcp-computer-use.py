#!/usr/bin/env python3
"""
MCP-совместимый сервер для Computer Use на Windows.
Запускается Hermes через stdio MCP транспорт.
Коннектится к bridge на Windows через WebSocket.

Hermes → этот скрипт (stdio MCP) → ws://100.105.159.88:9877 → computer_use.py на Windows
"""
import asyncio, json, sys, os, uuid
from typing import Any

BRIDGE_URL = "ws://100.105.159.88:9877"

TOOLS = [
    {"name": "computer_screenshot", "description": "Скриншот экрана Windows", "inputSchema": {
        "type": "object", "properties": {"region": {"type": "array", "items": {"type": "integer"}, "description": "[x,y,w,h] — область или весь экран"}},
    }},
    {"name": "computer_click", "description": "Клик мышью", "inputSchema": {
        "type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right"], "default": "left"}
        }, "required": ["x", "y"]
    }},
    {"name": "computer_double_click", "description": "Двойной клик", "inputSchema": {
        "type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]
    }},
    {"name": "computer_type", "description": "Напечатать текст (эмулирует клавиатуру)", "inputSchema": {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]
    }},
    {"name": "computer_press_key", "description": "Нажать клавишу(и). Примеры: enter, ctrl+c, alt+tab", "inputSchema": {
        "type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]
    }},
    {"name": "computer_scroll", "description": "Прокрутка. -3=вниз, 3=вверх", "inputSchema": {
        "type": "object", "properties": {"amount": {"type": "integer", "default": -3}, "x": {"type": "integer"}, "y": {"type": "integer"}},
    }},
    {"name": "computer_move_mouse", "description": "Передвинуть мышь", "inputSchema": {
        "type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]
    }},
    {"name": "computer_get_screen_size", "description": "Размер экрана", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "computer_get_active_window", "description": "Информация об активном окне", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "computer_list_windows", "description": "Список окон", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "computer_focus_window", "description": "Сфокусировать окно по названию", "inputSchema": {
        "type": "object", "properties": {"title": {"type": "string"}, "substring": {"type": "boolean", "default": True}}, "required": ["title"]
    }},
    {"name": "computer_launch_app", "description": "Запустить программу", "inputSchema": {
        "type": "object", "properties": {"path": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}}, "required": ["path"]
    }},
    {"name": "computer_kill_process", "description": "Убить процесс по имени или PID", "inputSchema": {
        "type": "object", "properties": {"name_or_pid": {"type": "string"}}, "required": ["name_or_pid"]
    }},
    {"name": "computer_read_file", "description": "Прочитать файл на Windows по полному пути", "inputSchema": {
        "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]
    }},
    {"name": "computer_write_file", "description": "Записать файл на Windows", "inputSchema": {
        "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]
    }},
    {"name": "computer_clipboard_get", "description": "Прочитать буфер обмена Windows", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "computer_clipboard_set", "description": "Записать в буфер обмена Windows", "inputSchema": {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]
    }},
]

TOOL_MAP = {
    "computer_screenshot": "screenshot",
    "computer_click": "click",
    "computer_double_click": "double_click",
    "computer_type": "type_text",
    "computer_press_key": "press_key",
    "computer_scroll": "scroll",
    "computer_move_mouse": "move_mouse",
    "computer_get_screen_size": "get_screen_size",
    "computer_get_active_window": "get_active_window",
    "computer_list_windows": "list_windows",
    "computer_focus_window": "focus_window",
    "computer_launch_app": "launch_app",
    "computer_kill_process": "kill_process",
    "computer_read_file": "read_file",
    "computer_write_file": "write_file",
    "computer_clipboard_get": "clipboard_get",
    "computer_clipboard_set": "clipboard_set",
}

async def send_to_bridge(action: str, params: dict) -> dict:
    import websockets
    async with websockets.connect(BRIDGE_URL, ping_interval=30, close_timeout=5) as ws:
        msg = json.dumps({"action": action, "params": params, "id": str(uuid.uuid4())[:8]})
        await ws.send(msg)
        raw = await asyncio.wait_for(ws.recv(), timeout=30)
        return json.loads(raw)


async def handle_request(request: dict) -> dict:
    method = request.get("method", "")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request["id"], "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "hermes-computer-use", "version": "1.0"}
        }}

    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": request["id"], "result": {"tools": TOOLS}}

    elif method == "tools/call":
        tool_name = request["params"]["name"]
        tool_args = request["params"].get("arguments", {})
        action = TOOL_MAP.get(tool_name)
        if not action:
            return {"jsonrpc": "2.0", "id": request["id"], "result": {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}]}}

        try:
            result = await send_to_bridge(action, tool_args)
            if result.get("ok"):
                data = result.get("data", "done")
                text = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
            else:
                text = f"ERROR: {result.get('error', 'unknown')}"
            return {"jsonrpc": "2.0", "id": request["id"], "result": {"content": [{"type": "text", "text": text}]}}
        except Exception as e:
            error_text = f"Bridge connection failed: {e}. Is Hermes Bridge running on Windows? ws://100.105.159.88:9877"
            return {"jsonrpc": "2.0", "id": request["id"], "result": {"content": [{"type": "text", "text": error_text}]}}

    elif method == "notifications/initialized":
        return None

    return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32601, "message": f"Method not found: {method}"}}


async def main():
    reader = asyncio.StreamReader()
    loop = asyncio.get_event_loop()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)

    buffer = b""
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=3600)
        except asyncio.TimeoutError:
            continue
        if not chunk:
            break
        buffer += chunk

        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = await handle_request(request)
                if response:
                    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
