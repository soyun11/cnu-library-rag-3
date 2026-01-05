# -*- coding: utf-8 -*-
"""
CNU Library MCP Server (Extended)
시설 정보 + 도서 검색/대출확인/픽업신청 기능 통합

사용법:
  - MCP 모드: python server.py --mcp
  - CLI 테스트: python server.py
"""

import os
import re
import json
import asyncio
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv

# MCP SDK
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    Resource,
)

# 도서 크롤러
from book_crawler import (
    BookCrawler,
    search_book_async,
    check_book_availability_async,
    login_async,
    request_pickup_async,
    get_my_loans_async,
)

load_dotenv()

# ----------------------------
# 설정
# ----------------------------
URL_326 = "https://library.cnu.ac.kr/webcontent/info/326"
BASE_URL = "https://library.cnu.ac.kr"

# ----------------------------
# 시설 데이터
# ----------------------------
FACILITY_DATA = {
    "신축도서관": {
        "지하 2층": [
            {"name": "열람실, 1인 캐럴", "desc": "학내 구성원 전용 개인 학습 공간(지정좌석)", "hours": "매일 06:00~23:00"},
            {"name": "스트레스프리존", "desc": "스트레스 해소 및 휴식 공간", "hours": "매일 06:00~23:00"},
            {"name": "그룹스터디룸", "desc": "6~8인 그룹 학습 공간", "hours": "평일 06:00~23:00"},
            {"name": "미디어제작실", "desc": "동영상 촬영 공간 지원(※ 사전 신청)", "hours": "평일 09:00~18:00"},
            {"name": "보존서고", "desc": "보존자료", "hours": "개인 이용 불가"}
        ],
        "지하 1층": [
            {"name": "열람실", "desc": "스마트 기기 및 노트북 활용 학습 공간", "hours": "매일 06:00~23:00"},
            {"name": "북카페", "desc": "카페형 학습 공간", "hours": "매일 06:00~23:00"},
            {"name": "스트레스프리존", "desc": "스트레스 해소 및 휴식 공간(야외)", "hours": "매일 06:00~23:00"},
            {"name": "작은아우름", "desc": "Walk Station 및 실내 정원을 갖춘 휴식 공간", "hours": "매일 06:00~23:00"},
            {"name": "미디어존", "desc": "컴퓨터 활용 학습 공간", "hours": "매일 06:00~23:00"},
            {"name": "강당", "desc": "행사, 공연 등 지원 공간", "hours": "평일 09:00~18:00"},
            {"name": "세미나실", "desc": "세미나, 회의 등 지원 공간", "hours": "평일 09:00~18:00"},
            {"name": "교육실", "desc": "PC를 활용한 이용자 교육 지원 공간", "hours": "평일 09:00~18:00"}
        ],
        "1층": [
            {"name": "대출실", "desc": "자료 대출, 도서관 이용 지원, 졸업앨범", "hours": "평일 09:00~18:00"},
            {"name": "갤러리", "desc": "전시", "hours": "평일 09:00~18:00"}
        ],
        "2층": [
            {"name": "연속간행물실", "desc": "국내외 연속간행물, 신문", "hours": "평일 09:00~18:00"},
            {"name": "제1자료실", "desc": "사회과학(300), 기술과학(500)", "hours": "평일 09:00~18:00"},
            {"name": "컨퍼런스룸", "desc": "10~14인 그룹 학습 공간", "hours": "평일 06:00~23:00"}
        ],
        "3층": [
            {"name": "제2자료실", "desc": "총류(000), 철학(100), 종교(200), 언어(400), 예술(600), 문학(700), 역사(900)", "hours": "평일 09:00~18:00"},
            {"name": "멀티미디어실", "desc": "멀티미디어 자료 열람 공간, VR 체험 부스", "hours": "평일 09:00~18:00"},
            {"name": "고서실", "desc": "고서, 귀중서", "hours": "평일 09:00~18:00"},
            {"name": "학위논문실", "desc": "학위논문", "hours": "평일 09:00~18:00"}
        ]
    },
    "중앙도서관": {
        "1층": [
            {"name": "아우름(자유열람실)", "desc": "개인 학습 공간", "hours": "24시간 운영"},
            {"name": "매점, 링크라운지", "desc": "휴게 및 음식 취식 공간", "hours": "24시간 운영"},
            {"name": "카페(99th Street)", "desc": "커피 등 음료 판매", "hours": "평일 08:30~17:30"}
        ],
        "2층": [
            {"name": "제3열람실", "desc": "학내 구성원 전용 개인 학습 공간", "hours": "매일 06:00~23:00"},
            {"name": "제1열람실", "desc": "학내 구성원 전용 개인 학습 공간", "hours": "휴실"},
            {"name": "제2열람실", "desc": "학내 구성원 전용 개인 학습 공간", "hours": "매일 06:00~23:00"}
        ]
    },
    "별관(자연과학도서관)": {
        "1층": [
            {"name": "제3자료실(자연과학)", "desc": "이학, 공학, 농학 등", "hours": "평일 09:00~18:00"}
        ]
    }
}


def norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def flatten_facilities() -> List[Dict]:
    result = []
    for section, floors in FACILITY_DATA.items():
        for floor, facilities in floors.items():
            for f in facilities:
                result.append({
                    "section": section,
                    "floor": floor,
                    "name": f["name"],
                    "desc": f["desc"],
                    "hours": f["hours"]
                })
    return result


FLAT_FACILITIES = flatten_facilities()


# ============================================================
# 시설 관련 Tool 함수들
# ============================================================

def search_facility(facility_name: str, floor: Optional[str] = None, section: Optional[str] = None) -> Dict[str, Any]:
    """시설 정보 검색"""
    results = []
    fn = norm(facility_name)
    
    for f in FLAT_FACILITIES:
        rn = norm(f["name"])
        if fn not in rn and rn not in fn and fn != rn:
            continue
        if floor and norm(floor) not in norm(f["floor"]):
            continue
        if section and norm(section) not in norm(f["section"]):
            continue
        results.append(f)
    
    return {
        "success": len(results) > 0,
        "query": {"facility_name": facility_name, "floor": floor, "section": section},
        "count": len(results),
        "facilities": results,
        "source_url": URL_326
    }


def get_operating_hours(facility_name: str) -> Dict[str, Any]:
    """시설 운영시간 조회"""
    results = []
    fn = norm(facility_name)
    
    for f in FLAT_FACILITIES:
        rn = norm(f["name"])
        if fn in rn or rn in fn:
            results.append({
                "name": f["name"],
                "location": f"{f['section']} {f['floor']}",
                "hours": f["hours"],
                "description": f["desc"]
            })
    
    return {
        "success": len(results) > 0,
        "query": facility_name,
        "results": results,
        "source_url": URL_326
    }


def list_floor_facilities(floor: str, section: Optional[str] = None) -> Dict[str, Any]:
    """특정 층의 모든 시설 조회"""
    results = []
    fl = norm(floor)
    
    for f in FLAT_FACILITIES:
        rf = norm(f["floor"])
        if fl not in rf and rf not in fl:
            continue
        if section and norm(section) not in norm(f["section"]):
            continue
        results.append(f)
    
    return {
        "success": len(results) > 0,
        "query": {"floor": floor, "section": section},
        "floor": floor,
        "count": len(results),
        "facilities": results,
        "source_url": URL_326
    }


def find_study_space(space_type: str = "individual") -> Dict[str, Any]:
    """학습 공간 찾기"""
    individual_keywords = ["열람실", "캐럴", "1인", "개인", "아우름"]
    group_keywords = ["그룹", "스터디룸", "컨퍼런스", "세미나"]
    
    keywords = individual_keywords if space_type == "individual" else group_keywords
    
    results = []
    for f in FLAT_FACILITIES:
        name_desc = f["name"] + f["desc"]
        if any(kw in name_desc for kw in keywords):
            results.append(f)
    
    return {
        "success": len(results) > 0,
        "space_type": space_type,
        "count": len(results),
        "facilities": results,
        "source_url": URL_326
    }


def find_food_places() -> Dict[str, Any]:
    """식사/음료 가능 장소 검색"""
    keywords = ["카페", "매점", "북카페", "라운지", "99th"]
    
    results = []
    for f in FLAT_FACILITIES:
        if any(kw in f["name"] or kw in f["desc"] for kw in keywords):
            results.append(f)
    
    return {
        "success": len(results) > 0,
        "count": len(results),
        "facilities": results,
        "source_url": URL_326
    }


def get_all_facilities() -> Dict[str, Any]:
    """전체 시설 목록 조회"""
    return {
        "success": True,
        "count": len(FLAT_FACILITIES),
        "facilities": FLAT_FACILITIES,
        "source_url": URL_326
    }


# ============================================================
# Tool 레지스트리
# ============================================================

TOOLS = {
    # --- 시설 관련 ---
    "search_facility": {
        "function": search_facility,
        "description": "도서관 내 특정 시설(북카페, 열람실, 스트레스프리존 등)의 위치, 설명, 운영시간을 검색합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "facility_name": {"type": "string", "description": "검색할 시설 이름 (예: 북카페, 열람실, 스트레스프리존)"},
                "floor": {"type": "string", "description": "층 필터 (예: 지하 1층, 2층) - 선택사항"},
                "section": {"type": "string", "description": "건물 필터 (예: 신축도서관, 중앙도서관) - 선택사항"}
            },
            "required": ["facility_name"]
        }
    },
    "get_operating_hours": {
        "function": get_operating_hours,
        "description": "특정 시설의 운영시간을 조회합니다. 예: 북카페 운영시간, 열람실 언제까지",
        "parameters": {
            "type": "object",
            "properties": {
                "facility_name": {"type": "string", "description": "시설 이름"}
            },
            "required": ["facility_name"]
        }
    },
    "list_floor_facilities": {
        "function": list_floor_facilities,
        "description": "특정 층에 있는 모든 시설 목록을 조회합니다. 예: 지하 1층에 뭐 있어?",
        "parameters": {
            "type": "object",
            "properties": {
                "floor": {"type": "string", "description": "층 (예: 지하 1층, 지하 2층, 1층, 2층, 3층)"},
                "section": {"type": "string", "description": "건물 필터 (예: 신축도서관) - 선택사항"}
            },
            "required": ["floor"]
        }
    },
    "find_study_space": {
        "function": find_study_space,
        "description": "학습 공간을 찾습니다. 개인 학습(individual) 또는 그룹 학습(group) 공간을 선택할 수 있습니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "space_type": {
                    "type": "string",
                    "enum": ["individual", "group"],
                    "description": "individual: 개인 학습 공간, group: 그룹 학습 공간"
                }
            },
            "required": ["space_type"]
        }
    },
    "find_food_places": {
        "function": find_food_places,
        "description": "도서관 내 카페, 매점 등 식사나 음료를 구매할 수 있는 장소를 찾습니다.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    "get_all_facilities": {
        "function": get_all_facilities,
        "description": "도서관의 모든 시설 전체 목록을 조회합니다.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    
    # --- 도서 관련 ---
    "search_book": {
        "function": None,  # async 함수로 별도 처리
        "description": "도서관 소장 도서를 검색합니다. 도서명, 저자명, ISBN 등으로 검색할 수 있습니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어 (도서명, 저자명, ISBN 등)"},
                "max_results": {"type": "integer", "description": "최대 결과 수 (기본: 10)", "default": 10}
            },
            "required": ["query"]
        }
    },
    "check_book_availability": {
        "function": None,  # async 함수로 별도 처리
        "description": "특정 도서의 대출 가능 여부를 확인합니다. search_book으로 얻은 book_id를 사용합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string", "description": "도서 ID (검색 결과에서 획득)"}
            },
            "required": ["book_id"]
        }
    },
    "library_login": {
        "function": None,  # async 함수로 별도 처리
        "description": "도서관에 로그인합니다. 픽업 서비스 신청이나 대출 현황 조회에 필요합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "학번 또는 교번"},
                "password": {"type": "string", "description": "비밀번호"}
            },
            "required": ["user_id", "password"]
        }
    },
    "request_book_pickup": {
        "function": None,  # async 함수로 별도 처리
        "description": "분관대출 서비스를 신청합니다. 중앙도서관 도서를 농학/법학/의학도서관에서 수령할 수 있습니다. 로그인이 필요합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string", "description": "도서 ID"},
                "pickup_location": {
                    "type": "string", 
                    "description": "수령 희망 분관 (농학도서관, 법학도서관, 의학도서관)", 
                    "default": "농학도서관",
                    "enum": ["농학도서관", "법학도서관", "의학도서관"]
                }
            },
            "required": ["book_id"]
        }
    },
    "get_my_loans": {
        "function": None,  # async 함수로 별도 처리
        "description": "분관대출 신청 현황을 조회합니다. 로그인이 필요합니다.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
}


# ============================================================
# MCP Server 설정
# ============================================================

mcp_server = Server("cnu-library")


@mcp_server.list_tools()
async def list_tools() -> List[Tool]:
    """MCP Tool 목록 반환"""
    tools = []
    for name, info in TOOLS.items():
        tools.append(Tool(
            name=name,
            description=info["description"],
            inputSchema=info["parameters"]
        ))
    return tools


@mcp_server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """MCP Tool 실행"""
    if name not in TOOLS:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    
    try:
        # 도서 관련 함수는 async 처리
        if name == "search_book":
            result = await search_book_async(**arguments)
        elif name == "check_book_availability":
            result = await check_book_availability_async(**arguments)
        elif name == "library_login":
            result = await login_async(**arguments)
        elif name == "request_book_pickup":
            result = await request_pickup_async(**arguments)
        elif name == "get_my_loans":
            result = await get_my_loans_async()
        else:
            # 동기 함수 (시설 관련)
            func = TOOLS[name]["function"]
            result = func(**arguments)
        
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


@mcp_server.list_resources()
async def list_resources() -> List[Resource]:
    """MCP Resource 목록"""
    return [
        Resource(
            uri="library://facilities",
            name="전체 시설 목록",
            description="충남대학교 도서관 전체 시설 정보",
            mimeType="application/json"
        ),
        Resource(
            uri="library://hours",
            name="운영시간 정보",
            description="모든 시설의 운영시간 정보",
            mimeType="application/json"
        )
    ]


@mcp_server.read_resource()
async def read_resource(uri: str) -> str:
    """MCP Resource 읽기"""
    if uri == "library://facilities":
        return json.dumps(FLAT_FACILITIES, ensure_ascii=False, indent=2)
    elif uri == "library://hours":
        hours_data = [{"name": f["name"], "location": f"{f['section']} {f['floor']}", "hours": f["hours"]} for f in FLAT_FACILITIES]
        return json.dumps(hours_data, ensure_ascii=False, indent=2)
    else:
        return json.dumps({"error": "Resource not found"})


# ============================================================
# MCP 서버 실행
# ============================================================

async def run_mcp_server():
    """MCP 서버 실행 (stdio)"""
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(read_stream, write_stream, mcp_server.create_initialization_options())


# ============================================================
# CLI 테스트
# ============================================================

async def run_cli_async():
    """CLI 모드 (async)"""
    print("=" * 60)
    print("CNU Library MCP Server (Extended)")
    print("=" * 60)
    print(f"Facilities: {len(FLAT_FACILITIES)}")
    print(f"Tools: {len(TOOLS)}")
    print("\n[시설 관련]")
    print("  - search_facility, get_operating_hours, list_floor_facilities")
    print("  - find_study_space, find_food_places, get_all_facilities")
    print("\n[도서 관련]")
    print("  - search_book, check_book_availability")
    print("  - library_login, request_book_pickup, get_my_loans")
    print("\nType 'q' to quit")
    print("=" * 60)
    
    while True:
        try:
            query = input("\nQuery> ").strip()
        except EOFError:
            break
            
        if not query:
            continue
        if query.lower() in ("q", "quit", "exit"):
            print("Bye!")
            break
        
        query_lower = query.lower()
        query_norm = norm(query)
        
        # 도서 검색
        if "책" in query or "도서" in query or "검색" in query:
            search_term = re.sub(r"(책|도서|찾아|검색|있어|알려줘|줘|해줘|\?)", "", query).strip()
            if search_term:
                print(f"\n📖 '{search_term}' 도서 검색 중...")
                result = await search_book_async(search_term, 5)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            continue
        
        # 시설 관련
        if "운영" in query or "시간" in query or "언제" in query:
            for f in FLAT_FACILITIES:
                if norm(f["name"].split(",")[0]) in query_norm:
                    result = get_operating_hours(f["name"].split(",")[0])
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    break
        
        elif "층" in query:
            floor_match = re.search(r"(지하\s*\d+\s*층|\d+\s*층)", query)
            if floor_match:
                result = list_floor_facilities(floor_match.group(1))
                print(json.dumps(result, ensure_ascii=False, indent=2))
        
        elif "카페" in query or "매점" in query or "먹" in query:
            result = find_food_places()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        
        elif "스터디" in query or "그룹" in query:
            result = find_study_space("group")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        
        else:
            found = False
            for f in FLAT_FACILITIES:
                if norm(f["name"].split(",")[0]) in query_norm:
                    result = search_facility(f["name"].split(",")[0])
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    found = True
                    break
            
            if not found:
                print("\n사용 예시:")
                print("  - '파이썬 책 찾아줘' - 도서 검색")
                print("  - '북카페 운영시간' - 시설 운영시간")
                print("  - '지하 1층에 뭐 있어?' - 층별 시설")


def run_cli():
    """CLI 실행"""
    asyncio.run(run_cli_async())


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp":
        asyncio.run(run_mcp_server())
    else:
        run_cli()