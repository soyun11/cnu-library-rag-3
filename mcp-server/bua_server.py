# -*- coding: utf-8 -*-
"""
BUA MCP Server
Browser Use Agent 기반 MCP 서버

Claude Desktop에서 자연어로 브라우저 자동화 명령 가능
"""

import asyncio
import json
import sys
import os

# BUA 모듈 경로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bua import (
    BrowserUseAgent,
    AgentConfig,
    anthropic_llm_callback,
    openai_llm_callback,
    SnapshotExtractor,
    snapshot_to_text
)


# ----------------------------
# 전역 에이전트 인스턴스
# ----------------------------
agent_instance: BrowserUseAgent = None


async def get_agent() -> BrowserUseAgent:
    """에이전트 싱글톤 인스턴스"""
    global agent_instance
    
    if agent_instance is None:
        config = AgentConfig(
            headless=False,  # 브라우저 창 보이게!
            max_steps=20
        )
        
        # LLM 콜백 설정 (환경변수에 따라)
        llm_callback = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            llm_callback = anthropic_llm_callback
        elif os.environ.get("OPENAI_API_KEY"):
            llm_callback = openai_llm_callback
        
        agent_instance = BrowserUseAgent(config=config, llm_callback=llm_callback)
        await agent_instance.initialize()
    
    return agent_instance


# ----------------------------
# MCP Tool 정의
# ----------------------------
TOOLS = {
    "browser_agent_run": {
        "description": "브라우저 에이전트를 실행하여 웹 작업을 자동화합니다. 자연어로 목표를 설명하면 AI가 알아서 브라우저를 조작합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "달성할 목표 (예: '네이버에서 날씨 검색해줘', '충남대 도서관에서 자바의 정석 검색해줘')"
                },
                "start_url": {
                    "type": "string",
                    "description": "시작 URL (선택사항)",
                    "default": ""
                }
            },
            "required": ["goal"]
        }
    },
    "browser_navigate": {
        "description": "지정한 URL로 브라우저를 이동합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "이동할 URL"
                }
            },
            "required": ["url"]
        }
    },
    "browser_snapshot": {
        "description": "현재 페이지의 DOM 스냅샷을 가져옵니다. 페이지 구조와 클릭 가능한 요소들을 확인할 수 있습니다.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    "browser_click": {
        "description": "지정한 CSS 셀렉터의 요소를 클릭합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "클릭할 요소의 CSS 셀렉터"
                }
            },
            "required": ["selector"]
        }
    },
    "browser_type": {
        "description": "지정한 입력 필드에 텍스트를 입력합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "입력 필드의 CSS 셀렉터"
                },
                "text": {
                    "type": "string",
                    "description": "입력할 텍스트"
                }
            },
            "required": ["selector", "text"]
        }
    },
    "browser_screenshot": {
        "description": "현재 페이지의 스크린샷을 저장합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "저장할 파일명",
                    "default": "screenshot.png"
                }
            }
        }
    }
}


# ----------------------------
# Tool 실행 함수
# ----------------------------
async def execute_tool(tool_name: str, params: dict) -> dict:
    """Tool 실행"""
    agent = await get_agent()
    
    if tool_name == "browser_agent_run":
        # 에이전트 자동 실행
        result = await agent.run(
            goal=params.get("goal"),
            start_url=params.get("start_url") or None
        )
        return result
    
    elif tool_name == "browser_navigate":
        from bua.tools import Action, ActionType
        result = await agent.tools.execute(
            Action(ActionType.NAVIGATE, value=params.get("url"))
        )
        return {"success": result.success, "message": result.message, "url": result.after_url}
    
    elif tool_name == "browser_snapshot":
        snapshot = await agent.snapshot_extractor.extract()
        return {
            "url": snapshot.url,
            "title": snapshot.title,
            "page_type": snapshot.page_type,
            "elements_count": len(snapshot.elements),
            "snapshot_text": snapshot_to_text(snapshot, max_elements=30)
        }
    
    elif tool_name == "browser_click":
        from bua.tools import Action, ActionType
        result = await agent.tools.execute(
            Action(ActionType.CLICK, selector=params.get("selector"))
        )
        return {"success": result.success, "message": result.message}
    
    elif tool_name == "browser_type":
        from bua.tools import Action, ActionType
        result = await agent.tools.execute(
            Action(ActionType.TYPE, selector=params.get("selector"), value=params.get("text"))
        )
        return {"success": result.success, "message": result.message}
    
    elif tool_name == "browser_screenshot":
        from bua.tools import Action, ActionType
        result = await agent.tools.execute(
            Action(ActionType.SCREENSHOT, value=params.get("filename", "screenshot.png"))
        )
        return {"success": result.success, "message": result.message, "path": result.screenshot_path}
    
    else:
        return {"error": f"Unknown tool: {tool_name}"}


# ----------------------------
# MCP 서버 핸들러
# ----------------------------
async def handle_mcp_request(request: dict) -> dict:
    """MCP 요청 처리"""
    method = request.get("method")
    params = request.get("params", {})
    request_id = request.get("id")
    
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "cnu-library-bua",
                    "version": "2.0.0"
                }
            }
        }
    
    elif method == "tools/list":
        tools_list = [
            {
                "name": name,
                "description": info["description"],
                "inputSchema": info["parameters"]
            }
            for name, info in TOOLS.items()
        ]
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": tools_list}
        }
    
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        
        try:
            result = await execute_tool(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                    ]
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(e)}
            }
    
    elif method == "notifications/initialized":
        return None  # 알림은 응답 불필요
    
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        }


async def run_mcp_server():
    """MCP 서버 실행 (stdio) - Windows 호환"""
    import sys
    
    print("BUA MCP Server started", file=sys.stderr)
    
    # Windows에서는 동기 I/O 사용
    while True:
        try:
            # stdin에서 한 줄 읽기 (동기)
            line = sys.stdin.readline()
            
            if not line:
                break
            
            line = line.strip()
            if not line:
                continue
            
            try:
                request = json.loads(line)
                print(f"Request: {request.get('method')}", file=sys.stderr)
                
                response = await handle_mcp_request(request)
                
                if response:
                    response_str = json.dumps(response)
                    print(response_str, flush=True)
                    print(f"Response sent for: {request.get('method')}", file=sys.stderr)
                    
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}", file=sys.stderr)
                
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            break
    
    # 정리
    if agent_instance:
        await agent_instance.close()
    
    print("BUA MCP Server stopped", file=sys.stderr)


# ----------------------------
# CLI 모드
# ----------------------------
async def run_cli():
    """CLI 테스트 모드"""
    import os
    from datetime import datetime
    
    print("=" * 60)
    print("BUA (Browser Use Agent) - CLI Mode")
    print("=" * 60)
    
    # 결과 저장용
    results = {
        "session_start": datetime.now().isoformat(),
        "actions": []
    }
    
    agent = await get_agent()
    
    while True:
        print("\nCommands:")
        print("  1. Agent Run (natural language goal)")
        print("  2. Navigate to URL")
        print("  3. Page Snapshot")
        print("  4. Click Element")
        print("  5. Type Text")
        print("  6. Press Key (Enter/Tab/etc)")
        print("  s. Save results to JSON")
        print("  q. Quit")
        
        cmd = input("Select> ").strip()
        
        if cmd == "q":
            break
        elif cmd == "s":
            # JSON 파일로 저장
            results["session_end"] = datetime.now().isoformat()
            filename = f"bua_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"\nSaved to: {filename}")
            
        elif cmd == "1":
            goal = input("Goal: ")
            start_url = input("Start URL (Enter=skip): ").strip() or None
            result = await agent.run(goal, start_url)
            results["actions"].append({
                "type": "agent_run",
                "goal": goal,
                "start_url": start_url,
                "result": result,
                "timestamp": datetime.now().isoformat()
            })
            print(f"\nResult:\n{json.dumps(result, ensure_ascii=False, indent=2)}")
            
        elif cmd == "2":
            url = input("URL: ")
            result = await execute_tool("browser_navigate", {"url": url})
            results["actions"].append({
                "type": "navigate",
                "url": url,
                "result": result,
                "timestamp": datetime.now().isoformat()
            })
            print(f"\nResult:\n{json.dumps(result, ensure_ascii=False, indent=2)}")
            
        elif cmd == "3":
            result = await execute_tool("browser_snapshot", {})
            results["actions"].append({
                "type": "snapshot",
                "result": result,
                "timestamp": datetime.now().isoformat()
            })
            print(f"\nResult:\n{json.dumps(result, ensure_ascii=False, indent=2)}")
            
        elif cmd == "4":
            selector = input("Selector: ")
            result = await execute_tool("browser_click", {"selector": selector})
            results["actions"].append({
                "type": "click",
                "selector": selector,
                "result": result,
                "timestamp": datetime.now().isoformat()
            })
            print(f"\nResult:\n{json.dumps(result, ensure_ascii=False, indent=2)}")
            
        elif cmd == "5":
            selector = input("Selector: ")
            text = input("Text: ")
            result = await execute_tool("browser_type", {"selector": selector, "text": text})
            results["actions"].append({
                "type": "type",
                "selector": selector,
                "text": text,
                "result": result,
                "timestamp": datetime.now().isoformat()
            })
            print(f"\nResult:\n{json.dumps(result, ensure_ascii=False, indent=2)}")
            
        elif cmd == "6":
            key = input("Key (Enter/Tab/Escape/etc): ")
            from bua.tools import Action, ActionType
            result = await agent.tools.execute(Action(ActionType.PRESS_KEY, value=key))
            result_dict = {"success": result.success, "message": result.message}
            results["actions"].append({
                "type": "press_key",
                "key": key,
                "result": result_dict,
                "timestamp": datetime.now().isoformat()
            })
            print(f"\nResult:\n{json.dumps(result_dict, ensure_ascii=False, indent=2)}")
    
    # 종료 시 자동 저장
    results["session_end"] = datetime.now().isoformat()
    filename = f"bua_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to: {filename}")
    
    await agent.close()


# ----------------------------
# 메인
# ----------------------------
if __name__ == "__main__":
    if "--mcp" in sys.argv:
        asyncio.run(run_mcp_server())
    else:
        asyncio.run(run_cli())