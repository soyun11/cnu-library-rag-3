# -*- coding: utf-8 -*-
"""
BUA Agent Module
LLM 기반 브라우저 자동화 에이전트

참고: Browser Use Agent 개발 여정 (김기훈, Samsung Research)
- Pre-analysis: 페이지 구조 분석
- ReAct Loop: Thought → Action → Observation → Reflection
- Post-summary: 페이지 전환 시 작업 요약
"""

import asyncio
import json
import re
import os
import sys
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, asdict, field
from datetime import datetime
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

# .env 파일 로드
from dotenv import load_dotenv
load_dotenv()

from .snapshot import SnapshotExtractor, PageSnapshot, snapshot_to_text
from .tools import BrowserTools, Action, ActionResult, ActionType, parse_action_from_dict

# Langfuse 추적 (선택적)
LANGFUSE_AVAILABLE = False
langfuse_client = None

def init_langfuse():
    """Langfuse 초기화 (최신 SDK v3 방식)"""
    global LANGFUSE_AVAILABLE, langfuse_client
    
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    
    if not public_key or not secret_key:
        print("[Langfuse] API keys not set. Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY", file=sys.stderr)
        return None
    
    try:
        # 환경변수 설정 (langfuse가 자동으로 읽음)
        os.environ["LANGFUSE_HOST"] = base_url
        
        from langfuse import Langfuse
        langfuse_client = Langfuse()
        LANGFUSE_AVAILABLE = True
        print(f"[Langfuse] Initialized successfully ({base_url})", file=sys.stderr)
        return langfuse_client
    except ImportError:
        print("[Langfuse] langfuse package not installed. Run: pip install langfuse", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[Langfuse] Initialization error: {e}", file=sys.stderr)
        return None


@dataclass
class AgentConfig:
    """에이전트 설정"""
    max_steps: int = 30                    # 최대 액션 수
    max_retries: int = 3                   # 실패 시 재시도 횟수
    timeout: int = 60000                   # 타임아웃 (ms)
    headless: bool = False                 # 헤드리스 모드
    viewport_width: int = 1280
    viewport_height: int = 900
    locale: str = "ko-KR"
    llm_provider: str = "anthropic"        # anthropic, openai, local


@dataclass
class StepRecord:
    """단계 기록"""
    step: int
    thought: str
    action: Dict[str, Any]
    observation: str
    success: bool
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class PageSummary:
    """페이지 작업 요약 (Post-summary)"""
    url: str
    page_type: str
    completed_actions: List[str]
    learned_rules: List[str]
    next_expected: str


class BrowserUseAgent:
    """Browser Use Agent - LLM 기반 브라우저 자동화"""
    
    def __init__(self, config: AgentConfig = None, llm_callback: Callable = None):
        """
        Args:
            config: 에이전트 설정
            llm_callback: LLM 호출 함수 (prompt: str) -> str
        """
        self.config = config or AgentConfig()
        self.llm_callback = llm_callback
        
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.tools: Optional[BrowserTools] = None
        self.snapshot_extractor: Optional[SnapshotExtractor] = None
        
        self.step_history: List[StepRecord] = []
        self.page_summaries: List[PageSummary] = []
        self.current_goal: str = ""
        self.is_running: bool = False
        
        # Langfuse 추적
        self.current_trace = None
        self.langfuse = None
    
    async def initialize(self):
        """브라우저 초기화"""
        import sys
        
        playwright = await async_playwright().start()
        
        self.browser = await playwright.chromium.launch(
            headless=self.config.headless
        )
        
        self.context = await self.browser.new_context(
            viewport={
                "width": self.config.viewport_width,
                "height": self.config.viewport_height
            },
            locale=self.config.locale
        )
        
        self.page = await self.context.new_page()
        self.tools = BrowserTools(self.page)
        self.snapshot_extractor = SnapshotExtractor(self.page)
        
        print("[Agent] Browser initialized", file=sys.stderr)
    
    async def close(self):
        """브라우저 종료"""
        import sys
        
        if self.browser:
            await self.browser.close()
            print("[Agent] Browser closed", file=sys.stderr)
    
    async def run(self, goal: str, start_url: str = None) -> Dict[str, Any]:
        """
        에이전트 실행
        
        Args:
            goal: 달성할 목표
            start_url: 시작 URL (선택)
        
        Returns:
            실행 결과
        """
        if not self.browser:
            await self.initialize()
        
        # 인코딩 문제 해결
        goal_clean = goal.encode('utf-8', errors='replace').decode('utf-8')
        self.current_goal = goal_clean
        self.step_history = []
        self.is_running = True
        
        print(f"[Agent] Goal: {goal_clean}", file=sys.stderr)
        
        # Langfuse trace (generation만 사용 - trace는 SDK v3에서 다름)
        self.current_trace = None  # trace 비활성화, generation만 사용
        
        # 시작 URL로 이동
        if start_url:
            await self.tools.execute(Action(ActionType.NAVIGATE, value=start_url))
            await asyncio.sleep(2)
        
        # ReAct 루프
        step = 0
        last_url = ""
        
        while self.is_running and step < self.config.max_steps:
            step += 1
            print(f"[Agent] Step {step}", file=sys.stderr)
            
            # 페이지 전환 감지 → Post-summary
            current_url = self.page.url
            if last_url and current_url != last_url:
                summary = await self._post_summary(last_url)
                self.page_summaries.append(summary)
                print(f"[Agent] Post-summary: {len(summary.completed_actions)} actions", file=sys.stderr)
                # 페이지 이동 후 안정화 대기
                await asyncio.sleep(2)
            
            # Pre-analysis: 페이지 스냅샷 (재시도 로직 추가)
            snapshot = None
            for retry in range(3):
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                    snapshot = await self.snapshot_extractor.extract()
                    break
                except Exception as e:
                    print(f"[Agent] Snapshot retry {retry+1}/3: {e}", file=sys.stderr)
                    await asyncio.sleep(1)
            
            if not snapshot:
                print("[Agent] Snapshot failed after retries", file=sys.stderr)
                break
            
            snapshot_text = snapshot_to_text(snapshot)
            
            print(f"[Agent] Snapshot: {snapshot.page_type}, {len(snapshot.elements)} elements", file=sys.stderr)
            
            # LLM에게 다음 액션 질의
            action_dict = await self._select_action(snapshot_text, step)
            
            if not action_dict:
                print("[Agent] Action selection failed", file=sys.stderr)
                break
            
            print(f"[Agent] Thought: {action_dict.get('reason', '')}", file=sys.stderr)
            print(f"[Agent] Action: {action_dict.get('action')} | {action_dict.get('selector', action_dict.get('value', ''))}", file=sys.stderr)
            
            # 액션 실행
            action = parse_action_from_dict(action_dict)
            result = await self.tools.execute(action)
            
            print(f"[Agent] Observation: {result.message} | success={result.success}", file=sys.stderr)
            
            # 기록
            record = StepRecord(
                step=step,
                thought=action_dict.get('reason', ''),
                action=action_dict,
                observation=result.message,
                success=result.success
            )
            self.step_history.append(record)
            
            # 완료 확인
            if action.action_type == ActionType.DONE:
                print(f"[Agent] Done: {action.value}", file=sys.stderr)
                self.is_running = False
                break
            
            # 실패 시 Reflection
            if not result.success:
                reflection = await self._reflect_on_failure(action_dict, result)
                print(f"[Agent] Reflection: {reflection}", file=sys.stderr)
            
            last_url = current_url
            await asyncio.sleep(1)
        
        # 최종 결과
        result = {
            "success": not self.is_running or step < self.config.max_steps,
            "goal": goal,
            "steps": step,
            "history": [asdict(r) for r in self.step_history],
            "page_summaries": [asdict(s) for s in self.page_summaries],
            "final_url": self.page.url if self.page else ""
        }
        
        # Langfuse flush (generation은 openai_llm_callback에서 이미 기록됨)
        if langfuse_client:
            langfuse_client.flush()
            print(f"[Langfuse] Data flushed", file=sys.stderr)
        
        return result
    
    async def _select_action(self, snapshot_text: str, step: int) -> Optional[Dict[str, Any]]:
        """LLM에게 다음 액션 질의"""
        
        # 이전 액션 히스토리
        history_text = ""
        if self.step_history:
            recent = self.step_history[-5:]  # 최근 5개
            history_lines = []
            for r in recent:
                history_lines.append(f"Step {r.step}: {r.action.get('action')} -> {r.observation}")
            history_text = "\n".join(history_lines)
        
        # 페이지 요약 컨텍스트
        context_text = ""
        if self.page_summaries:
            recent_summary = self.page_summaries[-1]
            context_text = f"""
이전 페이지 작업 요약:
- URL: {recent_summary.url}
- 완료한 작업: {', '.join(recent_summary.completed_actions[:3])}
"""
        
        prompt = f"""당신은 브라우저 자동화 에이전트입니다.

## 목표
{self.current_goal}

## 현재 페이지 상태
{snapshot_text}

## 이전 액션 히스토리 (중요! 반드시 확인하세요)
{history_text if history_text else "없음"}

{context_text}

## 절대 규칙
1. 목표를 정확히 파악하세요! 검색만 요청하면 검색만, 로그인 요청하면 로그인만!
2. 이전 히스토리에서 이미 성공한 액션은 다시 하지 마세요!
3. 같은 셀렉터에 같은 액션을 반복하지 마세요!

## 목표별 행동 가이드

### 검색 요청인 경우 (예: "자바의 정석 검색해줘")
1단계: 팝업 닫기 (a.infoClose) - 있으면
2단계: 검색창에 검색어 입력 (input[name='q'])
3단계: 검색 버튼 클릭 (input.searchBtn) 또는 Enter
4단계: 검색 결과 확인 후 done
※ 로그인 불필요! 검색은 로그인 없이 가능!

### 로그인 요청인 경우 (예: "로그인해줘", 아이디/비밀번호 포함)
1단계: 팝업 닫기 (a.infoClose) - 있으면
2단계: 로그인 페이지로 이동 - 필요시
3단계: 학번/아이디 입력 (input#id, input[name='user_id'], input[name='id'] 등)
4단계: 비밀번호 입력 (input[name='password'], input[name='user_password'] 등)
5단계: 로그인 버튼 클릭 (button[type='submit'], input[type='submit'], .login-btn 등) 또는 Enter
6단계: 페이지 URL이 변경되었거나 "로그아웃" 텍스트가 보이면 done
※ Enter 후에도 로그인 페이지면 → 로그인 버튼 직접 클릭 시도!
※ 로그인 성공 확인: URL 변경, "로그아웃", "마이페이지", 사용자 이름 등

### 층별 안내 요청인 경우 (예: "3층에 뭐 있어?", "열람실 어디야?", "북카페 위치", "운영시간")
1단계: 시설 안내 페이지로 이동 (https://library.cnu.ac.kr/webcontent/info/326)
2단계: 페이지 내용에서 해당 시설/층 정보 찾기
3단계: 찾은 정보를 done으로 응답
※ 시설 안내 페이지 URL: https://library.cnu.ac.kr/webcontent/info/326

### 대출/예약 요청인 경우
- 로그인 필요 → 로그인 먼저 → 해당 작업 수행

## 충남대 도서관 사이트 정보
- 검색창: input[name='q']
- 검색버튼: input.searchBtn
- 로그인 폼: input#id (학번), input[name='password'] (비밀번호)
- 팝업 닫기: a.infoClose
- 시설안내 페이지: https://library.cnu.ac.kr/webcontent/info/326

## 사용 가능한 액션
- click: 요소 클릭 {{"action": "click", "selector": "CSS셀렉터"}}
- type: 텍스트 입력 {{"action": "type", "selector": "CSS셀렉터", "value": "입력할텍스트"}}
- press_key: 키 입력 {{"action": "press_key", "value": "Enter"}}
- done: 작업 완료 {{"action": "done", "value": "결과요약"}}
- navigate: 페이지 이동 {{"action": "navigate", "value": "URL"}}

## 지시사항
1. 목표를 먼저 파악하세요! "검색"이면 검색만, "로그인"이면 로그인만, "층별/위치/운영시간"이면 시설안내 페이지!
2. 층별 안내/시설 위치/운영시간 질문이면: https://library.cnu.ac.kr/webcontent/info/326 로 이동 → 정보 확인 → done
3. 검색 요청이면: 검색창(input[name='q'])에 입력 → 검색버튼 또는 Enter → done
4. 로그인 요청이면: ID입력 → PW입력 → 로그인 버튼 클릭 → URL 변경 확인 → done
5. Enter 눌렀는데 아직 로그인 페이지면 → 로그인 버튼(button, input[type='submit']) 클릭!
6. 이미 한 액션은 반복하지 마세요!

## 응답 형식 (JSON만 출력)
{{"action": "액션명", "selector": "셀렉터(필요시)", "value": "값(필요시)", "reason": "이 액션을 선택한 이유"}}
"""
        
        # LLM 호출
        if self.llm_callback:
            response = await self.llm_callback(prompt)
        else:
            # 콜백 없으면 기본 동작 (테스트용)
            response = await self._default_llm_response(prompt)
        
        # JSON 파싱
        try:
            # JSON 부분만 추출
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            else:
                print(f"[Agent] JSON 파싱 실패: {response[:200]}")
                return None
        except json.JSONDecodeError as e:
            print(f"[Agent] JSON 파싱 오류: {e}")
            return None
    
    async def _reflect_on_failure(self, action_dict: Dict, result: ActionResult) -> str:
        """실패 시 반성 및 분석"""
        
        prompt = f"""액션 실행이 실패했습니다. 원인을 분석하세요.

## 시도한 액션
{json.dumps(action_dict, ensure_ascii=False)}

## 결과
- 성공 여부: {result.success}
- 메시지: {result.message}
- 오류: {result.error}

## 분석 요청
1. 실패 원인은 무엇인가요?
2. 다음에 어떻게 해야 할까요?

간단히 1-2문장으로 답변하세요.
"""
        
        if self.llm_callback:
            return await self.llm_callback(prompt)
        else:
            return f"액션 실패: {result.error}. 다른 셀렉터를 시도하거나 페이지 상태를 다시 확인해야 합니다."
    
    async def _post_summary(self, url: str) -> PageSummary:
        """페이지 전환 시 작업 요약 생성"""
        
        # 해당 URL에서 수행한 액션들
        url_actions = [
            r for r in self.step_history 
            if r.success and r.action.get('action') != 'wait'
        ]
        
        completed = [
            f"{r.action.get('action')}: {r.action.get('selector', r.action.get('value', ''))}"
            for r in url_actions[-5:]
        ]
        
        return PageSummary(
            url=url,
            page_type="unknown",
            completed_actions=completed,
            learned_rules=[],
            next_expected=""
        )
    
    async def _default_llm_response(self, prompt: str) -> str:
        """기본 LLM 응답 (테스트/데모용)"""
        # 실제로는 LLM을 호출해야 함
        # 여기서는 간단한 규칙 기반 응답
        
        if "로그인" in self.current_goal and "login" in prompt.lower():
            return '{"action": "type", "selector": "input#id", "value": "testuser", "reason": "로그인을 위해 아이디 입력"}'
        
        if "검색" in self.current_goal:
            return '{"action": "type", "selector": "input[type=search]", "value": "검색어", "reason": "검색어 입력"}'
        
        return '{"action": "wait", "value": "2", "reason": "페이지 로딩 대기"}'


# Anthropic Claude API를 사용하는 LLM 콜백
async def anthropic_llm_callback(prompt: str) -> str:
    """Anthropic Claude API 호출 + Langfuse 추적"""
    
    try:
        import anthropic
        
        # 인코딩 문제 해결: surrogate 문자 제거
        prompt_clean = prompt.encode('utf-8', errors='replace').decode('utf-8')
        
        client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY")
        )
        
        # Langfuse generation 추적
        generation = None
        if langfuse_client:
            generation = langfuse_client.generation(
                name="anthropic-action-selection",
                model="claude-sonnet-4-20250514",
                input=prompt_clean[:500] + "..." if len(prompt_clean) > 500 else prompt_clean
            )
        
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[
                {"role": "user", "content": prompt_clean}
            ]
        )
        
        result = message.content[0].text
        
        # Langfuse generation 종료
        if generation:
            generation.end(
                output=result,
                usage={
                    "input": message.usage.input_tokens,
                    "output": message.usage.output_tokens,
                    "total": message.usage.input_tokens + message.usage.output_tokens
                }
            )
        
        return result
        
    except ImportError:
        print("[Agent] anthropic package not installed", file=sys.stderr)
        return '{"action": "wait", "value": "2", "reason": "LLM not configured"}'
    except Exception as e:
        print(f"[Agent] Anthropic API error: {e}", file=sys.stderr)
        return '{"action": "wait", "value": "2", "reason": "API error"}'


# OpenAI API를 사용하는 LLM 콜백  
async def openai_llm_callback(prompt: str) -> str:
    """OpenAI API 호출 (Langfuse 자동 추적)"""
    
    try:
        # 인코딩 문제 해결: surrogate 문자 제거
        prompt_clean = prompt.encode('utf-8', errors='replace').decode('utf-8')
        
        # Langfuse OpenAI wrapper 사용 (자동 추적)
        if LANGFUSE_AVAILABLE:
            from langfuse.openai import openai
        else:
            import openai
        
        client = openai.OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY")
        )
        
        response = client.chat.completions.create(
            name="bua-action-selection",  # Langfuse에서 보이는 이름
            model="gpt-4o",
            messages=[
                {"role": "user", "content": prompt_clean}
            ],
            max_tokens=1024,
            metadata={"agent": "bua", "type": "action_selection"}
        )
        
        return response.choices[0].message.content
        
    except ImportError as e:  # 'as e' 추가
            print(f"[Agent] ImportError details: {e}", file=sys.stderr)  # 진짜 에러 내용 출력
            print(f"[Agent] Search paths: {sys.path}", file=sys.stderr) # 파이썬 경로 확인
            return '{"action": "wait", "value": "2", "reason": "Import Error"}'
    except Exception as e:
        print(f"[Agent] OpenAI API error: {e}", file=sys.stderr)
        return '{"action": "wait", "value": "2", "reason": "API error"}'


# 테스트용
async def test_agent():
    """에이전트 테스트"""
    import sys
    
    config = AgentConfig(headless=False)
    agent = BrowserUseAgent(config=config, llm_callback=anthropic_llm_callback)
    
    try:
        result = await agent.run(
            goal="충남대학교 도서관에서 '자바의 정석' 책을 검색해줘",
            start_url="https://library.cnu.ac.kr"
        )
        
        print(f"[Result] success={result['success']}, steps={result['steps']}", file=sys.stderr)
        
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(test_agent())