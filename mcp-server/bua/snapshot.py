# -*- coding: utf-8 -*-
"""
BUA Snapshot Module
브라우저 페이지의 DOM 트리를 LLM이 이해할 수 있는 형태로 추출

참고: Browser Use Agent 개발 여정 (김기훈, Samsung Research)
- Snapshot: 구조적·의미적 정보 (role, attribute, clickable 여부)
- 스크롤 전 영역까지 포함 → 보이지 않는 요소까지 후보 탐색
- Selector/BBox로 근거 남김 → Explainability·디버깅 용이
"""

import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from playwright.async_api import Page, ElementHandle


@dataclass
class ElementInfo:
    """DOM 요소 정보"""
    index: int                    # 요소 인덱스 (LLM이 참조용)
    tag: str                      # 태그명 (button, input, a, ...)
    role: str                     # ARIA role
    text: str                     # 텍스트 내용
    href: Optional[str]           # 링크 URL
    placeholder: Optional[str]    # placeholder
    value: Optional[str]          # input value
    bbox: Dict[str, float]        # bounding box {x, y, width, height}
    selector: str                 # CSS selector
    is_clickable: bool            # 클릭 가능 여부
    is_editable: bool             # 편집 가능 여부
    is_visible: bool              # 화면에 보이는지
    attributes: Dict[str, str]    # 주요 속성들


@dataclass
class PageSnapshot:
    """페이지 스냅샷"""
    url: str
    title: str
    elements: List[ElementInfo]
    forms: List[Dict[str, Any]]
    page_type: str                # 페이지 유형 추정
    summary: str                  # 페이지 요약


class SnapshotExtractor:
    """DOM 스냅샷 추출기"""
    
    # 인터랙션 가능한 요소 태그
    INTERACTIVE_TAGS = [
        'a', 'button', 'input', 'select', 'textarea',
        'details', 'summary', '[onclick]', '[role="button"]',
        '[role="link"]', '[role="tab"]', '[role="menuitem"]',
        '[tabindex]'
    ]
    
    # 무시할 요소
    IGNORE_TAGS = ['script', 'style', 'noscript', 'meta', 'link', 'head']
    
    def __init__(self, page: Page):
        self.page = page
        self.element_index = 0
    
    async def extract(self) -> PageSnapshot:
        """페이지 스냅샷 추출"""
        self.element_index = 0
        
        url = self.page.url
        title = await self.page.title()
        
        # 인터랙션 가능한 요소들 추출
        elements = await self._extract_interactive_elements()
        
        # 폼 정보 추출
        forms = await self._extract_forms()
        
        # 페이지 유형 추정
        page_type = self._infer_page_type(url, title, elements)
        
        # 페이지 요약 생성
        summary = self._generate_summary(url, title, elements, forms)
        
        return PageSnapshot(
            url=url,
            title=title,
            elements=elements,
            forms=forms,
            page_type=page_type,
            summary=summary
        )
    
    async def _extract_interactive_elements(self) -> List[ElementInfo]:
        """인터랙션 가능한 요소들 추출"""
        elements = []
        
        # 복합 셀렉터로 한 번에 조회
        selector = ', '.join(self.INTERACTIVE_TAGS)
        
        try:
            handles = await self.page.query_selector_all(selector)
            
            for handle in handles:
                try:
                    element_info = await self._extract_element_info(handle)
                    if element_info and element_info.is_visible:
                        elements.append(element_info)
                except Exception as e:
                    continue
        except Exception as e:
            import sys
            print(f"[Snapshot] Element extraction error: {e}", file=sys.stderr)
        
        return elements
    
    async def _extract_element_info(self, handle: ElementHandle) -> Optional[ElementInfo]:
        """단일 요소 정보 추출"""
        try:
            # 기본 정보 추출
            tag = await handle.evaluate('el => el.tagName.toLowerCase()')
            
            # 무시할 태그 필터링
            if tag in self.IGNORE_TAGS:
                return None
            
            # 가시성 확인
            is_visible = await handle.is_visible()
            if not is_visible:
                return None
            
            # Bounding box
            bbox = await handle.bounding_box()
            if not bbox or bbox['width'] == 0 or bbox['height'] == 0:
                return None
            
            # 텍스트 내용
            text = await handle.evaluate('el => el.innerText || el.textContent || ""')
            text = text.strip()[:100] if text else ""  # 100자 제한
            
            # 속성들
            attrs = await handle.evaluate('''el => {
                const result = {};
                ['id', 'name', 'class', 'type', 'href', 'placeholder', 'value', 
                 'role', 'aria-label', 'title', 'alt', 'data-testid'].forEach(attr => {
                    if (el.getAttribute(attr)) {
                        result[attr] = el.getAttribute(attr);
                    }
                });
                return result;
            }''')
            
            # CSS selector 생성
            selector = await self._generate_selector(handle, attrs)
            
            # 클릭/편집 가능 여부
            is_clickable = tag in ['a', 'button'] or attrs.get('onclick') or attrs.get('role') == 'button'
            is_editable = tag in ['input', 'textarea', 'select'] or await handle.evaluate('el => el.isContentEditable')
            
            self.element_index += 1
            
            return ElementInfo(
                index=self.element_index,
                tag=tag,
                role=attrs.get('role', ''),
                text=text,
                href=attrs.get('href'),
                placeholder=attrs.get('placeholder'),
                value=attrs.get('value'),
                bbox={
                    'x': round(bbox['x'], 1),
                    'y': round(bbox['y'], 1),
                    'width': round(bbox['width'], 1),
                    'height': round(bbox['height'], 1)
                },
                selector=selector,
                is_clickable=is_clickable,
                is_editable=is_editable,
                is_visible=is_visible,
                attributes=attrs
            )
            
        except Exception as e:
            return None
    
    async def _generate_selector(self, handle: ElementHandle, attrs: Dict) -> str:
        """CSS selector 생성"""
        # 우선순위: id > name > data-testid > class + tag
        if attrs.get('id'):
            return f"#{attrs['id']}"
        if attrs.get('name'):
            tag = await handle.evaluate('el => el.tagName.toLowerCase()')
            return f"{tag}[name='{attrs['name']}']"
        if attrs.get('data-testid'):
            return f"[data-testid='{attrs['data-testid']}']"
        
        # 복합 셀렉터
        try:
            selector = await handle.evaluate('''el => {
                const tag = el.tagName.toLowerCase();
                const classes = Array.from(el.classList).slice(0, 2).join('.');
                if (classes) return tag + '.' + classes;
                return tag;
            }''')
            return selector
        except:
            return "unknown"
    
    async def _extract_forms(self) -> List[Dict[str, Any]]:
        """폼 정보 추출"""
        forms = []
        
        try:
            form_handles = await self.page.query_selector_all('form')
            
            for i, form in enumerate(form_handles):
                form_info = await form.evaluate('''form => {
                    const inputs = Array.from(form.querySelectorAll('input, select, textarea'));
                    return {
                        action: form.action || '',
                        method: form.method || 'get',
                        fields: inputs.map(el => ({
                            tag: el.tagName.toLowerCase(),
                            name: el.name || '',
                            type: el.type || '',
                            placeholder: el.placeholder || '',
                            required: el.required || false
                        }))
                    };
                }''')
                form_info['index'] = i
                forms.append(form_info)
                
        except Exception as e:
            import sys
            print(f"[Snapshot] Form extraction error: {e}", file=sys.stderr)
        
        return forms
    
    def _infer_page_type(self, url: str, title: str, elements: List[ElementInfo]) -> str:
        """페이지 유형 추정"""
        url_lower = url.lower()
        title_lower = title.lower()
        
        # URL/제목 기반 추정
        if 'login' in url_lower or '로그인' in title_lower:
            return 'login_page'
        if 'search' in url_lower or '검색' in title_lower:
            return 'search_page'
        if 'detail' in url_lower or '상세' in title_lower:
            return 'detail_page'
        if 'result' in url_lower or '결과' in title_lower:
            return 'result_page'
        if 'form' in url_lower or '신청' in title_lower or '작성' in title_lower:
            return 'form_page'
        if 'list' in url_lower or '목록' in title_lower:
            return 'list_page'
        
        # 요소 기반 추정
        editable_count = sum(1 for e in elements if e.is_editable)
        link_count = sum(1 for e in elements if e.tag == 'a')
        
        if editable_count >= 3:
            return 'form_page'
        if link_count >= 10:
            return 'list_page'
        
        return 'unknown'
    
    def _generate_summary(self, url: str, title: str, 
                          elements: List[ElementInfo], 
                          forms: List[Dict]) -> str:
        """Generate page summary"""
        clickable = [e for e in elements if e.is_clickable]
        editable = [e for e in elements if e.is_editable]
        
        summary_parts = [
            f"URL: {url}",
            f"Title: {title}",
            f"Clickable: {len(clickable)}",
            f"Editable: {len(editable)}",
            f"Forms: {len(forms)}"
        ]
        
        return " | ".join(summary_parts)


def snapshot_to_text(snapshot: PageSnapshot, max_elements: int = 50) -> str:
    """Convert snapshot to text for LLM prompt"""
    lines = [
        "=== Page Snapshot ===",
        f"URL: {snapshot.url}",
        f"Title: {snapshot.title}",
        f"Page Type: {snapshot.page_type}",
        "",
        "=== Interactive Elements ==="
    ]
    
    for elem in snapshot.elements[:max_elements]:
        elem_desc = f"[{elem.index}] <{elem.tag}>"
        
        if elem.text:
            # 한글 인코딩 문제 해결
            text_clean = elem.text[:30].encode('utf-8', errors='replace').decode('utf-8')
            elem_desc += f" '{text_clean}'"
        if elem.href:
            elem_desc += f" href='{elem.href[:50]}'"
        if elem.placeholder:
            placeholder_clean = elem.placeholder.encode('utf-8', errors='replace').decode('utf-8')
            elem_desc += f" placeholder='{placeholder_clean}'"
        if elem.role:
            elem_desc += f" role='{elem.role}'"
        
        elem_desc += f" | selector: {elem.selector}"
        
        if elem.is_clickable:
            elem_desc += " [clickable]"
        if elem.is_editable:
            elem_desc += " [editable]"
        
        lines.append(elem_desc)
    
    if len(snapshot.elements) > max_elements:
        lines.append(f"... and {len(snapshot.elements) - max_elements} more elements")
    
    if snapshot.forms:
        lines.append("")
        lines.append("=== Forms ===")
        for form in snapshot.forms:
            lines.append(f"Form {form['index']}: action={form['action']}, fields={len(form['fields'])}")
    
    return "\n".join(lines)


# 테스트용
async def test_snapshot():
    import sys
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        await page.goto("https://library.cnu.ac.kr")
        await asyncio.sleep(2)
        
        extractor = SnapshotExtractor(page)
        snapshot = await extractor.extract()
        
        print(snapshot_to_text(snapshot), file=sys.stderr)
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(test_snapshot())