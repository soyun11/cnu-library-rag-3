# -*- coding: utf-8 -*-
"""
BUA Tools Module
브라우저 제어를 위한 액션 도구들

참고: Browser Use Agent 개발 여정 (김기훈, Samsung Research)
- navigate(url): 페이지 이동
- click(selector): 요소 클릭
- type(selector, text): 텍스트 입력
- wait(seconds): 대기
"""

import asyncio
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
from playwright.async_api import Page


class ActionType(Enum):
    """액션 유형"""
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    SCROLL = "scroll"
    WAIT = "wait"
    PRESS_KEY = "press_key"
    HOVER = "hover"
    GO_BACK = "go_back"
    SCREENSHOT = "screenshot"
    DONE = "done"  # Task completed


@dataclass
class Action:
    """액션 정의"""
    action_type: ActionType
    selector: Optional[str] = None
    value: Optional[str] = None
    reason: str = ""


@dataclass 
class ActionResult:
    """액션 실행 결과"""
    success: bool
    message: str
    action: Action
    before_url: str
    after_url: str
    error: Optional[str] = None
    screenshot_path: Optional[str] = None


class BrowserTools:
    """브라우저 제어 도구"""
    
    def __init__(self, page: Page):
        self.page = page
        self.action_history: List[ActionResult] = []
    
    async def execute(self, action: Action) -> ActionResult:
        """액션 실행"""
        before_url = self.page.url
        
        try:
            if action.action_type == ActionType.NAVIGATE:
                result = await self._navigate(action.value)
            elif action.action_type == ActionType.CLICK:
                result = await self._click(action.selector)
            elif action.action_type == ActionType.TYPE:
                result = await self._type(action.selector, action.value)
            elif action.action_type == ActionType.SELECT:
                result = await self._select(action.selector, action.value)
            elif action.action_type == ActionType.SCROLL:
                result = await self._scroll(action.value)
            elif action.action_type == ActionType.WAIT:
                result = await self._wait(action.value)
            elif action.action_type == ActionType.PRESS_KEY:
                result = await self._press_key(action.value)
            elif action.action_type == ActionType.HOVER:
                result = await self._hover(action.selector)
            elif action.action_type == ActionType.GO_BACK:
                result = await self._go_back()
            elif action.action_type == ActionType.SCREENSHOT:
                result = await self._screenshot(action.value)
            elif action.action_type == ActionType.DONE:
                result = ActionResult(
                    success=True,
                    message="Task completed",
                    action=action,
                    before_url=before_url,
                    after_url=self.page.url
                )
            else:
                result = ActionResult(
                    success=False,
                    message=f"Unknown action: {action.action_type}",
                    action=action,
                    before_url=before_url,
                    after_url=self.page.url,
                    error="Unknown action type"
                )
            
            self.action_history.append(result)
            return result
            
        except Exception as e:
            result = ActionResult(
                success=False,
                message=f"Action execution error: {str(e)}",
                action=action,
                before_url=before_url,
                after_url=self.page.url,
                error=str(e)
            )
            self.action_history.append(result)
            return result
    
    async def _navigate(self, url: str) -> ActionResult:
        """페이지 이동"""
        before_url = self.page.url
        
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1)
            
            return ActionResult(
                success=True,
                message=f"Navigation completed: {url}",
                action=Action(ActionType.NAVIGATE, value=url),
                before_url=before_url,
                after_url=self.page.url
            )
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Navigation failed: {str(e)}",
                action=Action(ActionType.NAVIGATE, value=url),
                before_url=before_url,
                after_url=self.page.url,
                error=str(e)
            )
    
    async def _click(self, selector: str) -> ActionResult:
        """요소 클릭"""
        before_url = self.page.url
        action = Action(ActionType.CLICK, selector=selector)
        
        try:
            # 요소 찾기
            element = await self.page.wait_for_selector(selector, timeout=10000)
            
            if not element:
                return ActionResult(
                    success=False,
                    message=f"Element not found: {selector}",
                    action=action,
                    before_url=before_url,
                    after_url=self.page.url,
                    error="Element not found"
                )
            
            # 클릭 가능할 때까지 대기
            await element.scroll_into_view_if_needed()
            await asyncio.sleep(0.3)
            
            # 클릭
            await element.click()
            await asyncio.sleep(1)
            
            return ActionResult(
                success=True,
                message=f"Click completed: {selector}",
                action=action,
                before_url=before_url,
                after_url=self.page.url
            )
            
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Click failed: {str(e)}",
                action=action,
                before_url=before_url,
                after_url=self.page.url,
                error=str(e)
            )
    
    async def _type(self, selector: str, text: str) -> ActionResult:
        """텍스트 입력"""
        before_url = self.page.url
        action = Action(ActionType.TYPE, selector=selector, value=text)
        
        try:
            element = await self.page.wait_for_selector(selector, timeout=10000)
            
            if not element:
                return ActionResult(
                    success=False,
                    message=f"Element not found: {selector}",
                    action=action,
                    before_url=before_url,
                    after_url=self.page.url,
                    error="Element not found"
                )
            
            # 기존 내용 지우고 입력
            await element.click()
            await element.fill("")
            await element.fill(text)
            await asyncio.sleep(0.5)
            
            return ActionResult(
                success=True,
                message=f"Input completed: {selector} <- '{text[:20]}...'",
                action=action,
                before_url=before_url,
                after_url=self.page.url
            )
            
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Input failed: {str(e)}",
                action=action,
                before_url=before_url,
                after_url=self.page.url,
                error=str(e)
            )
    
    async def _select(self, selector: str, value: str) -> ActionResult:
        """드롭다운 선택"""
        before_url = self.page.url
        action = Action(ActionType.SELECT, selector=selector, value=value)
        
        try:
            element = await self.page.wait_for_selector(selector, timeout=10000)
            
            if not element:
                return ActionResult(
                    success=False,
                    message=f"Element not found: {selector}",
                    action=action,
                    before_url=before_url,
                    after_url=self.page.url,
                    error="Element not found"
                )
            
            # value 또는 label로 선택 시도
            try:
                await element.select_option(value=value)
            except:
                await element.select_option(label=value)
            
            await asyncio.sleep(0.5)
            
            return ActionResult(
                success=True,
                message=f"Selection completed: {selector} <- '{value}'",
                action=action,
                before_url=before_url,
                after_url=self.page.url
            )
            
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Selection failed: {str(e)}",
                action=action,
                before_url=before_url,
                after_url=self.page.url,
                error=str(e)
            )
    
    async def _scroll(self, direction: str) -> ActionResult:
        """스크롤"""
        before_url = self.page.url
        action = Action(ActionType.SCROLL, value=direction)
        
        try:
            if direction == "down":
                await self.page.evaluate("window.scrollBy(0, 500)")
            elif direction == "up":
                await self.page.evaluate("window.scrollBy(0, -500)")
            elif direction == "top":
                await self.page.evaluate("window.scrollTo(0, 0)")
            elif direction == "bottom":
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            
            await asyncio.sleep(0.5)
            
            return ActionResult(
                success=True,
                message=f"Scroll completed: {direction}",
                action=action,
                before_url=before_url,
                after_url=self.page.url
            )
            
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Scroll failed: {str(e)}",
                action=action,
                before_url=before_url,
                after_url=self.page.url,
                error=str(e)
            )
    
    async def _wait(self, seconds: str) -> ActionResult:
        """대기"""
        before_url = self.page.url
        action = Action(ActionType.WAIT, value=seconds)
        
        try:
            wait_time = float(seconds)
            await asyncio.sleep(wait_time)
            
            return ActionResult(
                success=True,
                message=f"{wait_time}초 Wait completed",
                action=action,
                before_url=before_url,
                after_url=self.page.url
            )
            
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Wait failed: {str(e)}",
                action=action,
                before_url=before_url,
                after_url=self.page.url,
                error=str(e)
            )
    
    async def _press_key(self, key: str) -> ActionResult:
        """키 입력"""
        before_url = self.page.url
        action = Action(ActionType.PRESS_KEY, value=key)
        
        try:
            await self.page.keyboard.press(key)
            await asyncio.sleep(0.5)
            
            return ActionResult(
                success=True,
                message=f"키 Input completed: {key}",
                action=action,
                before_url=before_url,
                after_url=self.page.url
            )
            
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"키 Input failed: {str(e)}",
                action=action,
                before_url=before_url,
                after_url=self.page.url,
                error=str(e)
            )
    
    async def _hover(self, selector: str) -> ActionResult:
        """마우스 호버"""
        before_url = self.page.url
        action = Action(ActionType.HOVER, selector=selector)
        
        try:
            element = await self.page.wait_for_selector(selector, timeout=10000)
            
            if element:
                await element.hover()
                await asyncio.sleep(0.5)
                
                return ActionResult(
                    success=True,
                    message=f"Hover completed: {selector}",
                    action=action,
                    before_url=before_url,
                    after_url=self.page.url
                )
            else:
                return ActionResult(
                    success=False,
                    message=f"Element not found: {selector}",
                    action=action,
                    before_url=before_url,
                    after_url=self.page.url,
                    error="Element not found"
                )
                
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Hover failed: {str(e)}",
                action=action,
                before_url=before_url,
                after_url=self.page.url,
                error=str(e)
            )
    
    async def _go_back(self) -> ActionResult:
        """뒤로가기"""
        before_url = self.page.url
        action = Action(ActionType.GO_BACK)
        
        try:
            await self.page.go_back()
            await asyncio.sleep(1)
            
            return ActionResult(
                success=True,
                message="Go back completed",
                action=action,
                before_url=before_url,
                after_url=self.page.url
            )
            
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Go back failed: {str(e)}",
                action=action,
                before_url=before_url,
                after_url=self.page.url,
                error=str(e)
            )
    
    async def _screenshot(self, filename: str = None) -> ActionResult:
        """Screenshot saved"""
        before_url = self.page.url
        
        if not filename:
            filename = f"screenshot_{len(self.action_history)}.png"
        
        action = Action(ActionType.SCREENSHOT, value=filename)
        
        try:
            await self.page.screenshot(path=filename, full_page=True)
            
            return ActionResult(
                success=True,
                message=f"Screenshot saved: {filename}",
                action=action,
                before_url=before_url,
                after_url=self.page.url,
                screenshot_path=filename
            )
            
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Screenshot failed: {str(e)}",
                action=action,
                before_url=before_url,
                after_url=self.page.url,
                error=str(e)
            )
    
    def get_tools_description(self) -> str:
        """도구 설명 (LLM 프롬프트용)"""
        return """
사용 가능한 도구:

1. navigate(url): 지정한 URL로 이동
   예: {"action": "navigate", "value": "https://example.com"}

2. click(selector): 요소 클릭
   예: {"action": "click", "selector": "#login-btn"}

3. type(selector, value): 텍스트 입력
   예: {"action": "type", "selector": "input#username", "value": "myid"}

4. select(selector, value): 드롭다운 선택
   예: {"action": "select", "selector": "select#country", "value": "Korea"}

5. scroll(direction): 스크롤 (up/down/top/bottom)
   예: {"action": "scroll", "value": "down"}

6. wait(seconds): 대기
   예: {"action": "wait", "value": "2"}

7. press_key(key): 키 입력 (Enter, Tab, Escape 등)
   예: {"action": "press_key", "value": "Enter"}

8. hover(selector): 마우스 호버
   예: {"action": "hover", "selector": ".dropdown-menu"}

9. go_back(): 뒤로가기
   예: {"action": "go_back"}

10. done(): Task completed
    예: {"action": "done", "value": "결과 요약 메시지"}
"""


def parse_action_from_dict(action_dict: Dict[str, Any]) -> Action:
    """딕셔너리에서 Action 객체 생성"""
    action_type_str = action_dict.get("action", "").lower()
    
    action_type_map = {
        "navigate": ActionType.NAVIGATE,
        "click": ActionType.CLICK,
        "type": ActionType.TYPE,
        "select": ActionType.SELECT,
        "scroll": ActionType.SCROLL,
        "wait": ActionType.WAIT,
        "press_key": ActionType.PRESS_KEY,
        "hover": ActionType.HOVER,
        "go_back": ActionType.GO_BACK,
        "screenshot": ActionType.SCREENSHOT,
        "done": ActionType.DONE
    }
    
    action_type = action_type_map.get(action_type_str, ActionType.WAIT)
    
    return Action(
        action_type=action_type,
        selector=action_dict.get("selector"),
        value=action_dict.get("value"),
        reason=action_dict.get("reason", "")
    )