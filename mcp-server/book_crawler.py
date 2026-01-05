# -*- coding: utf-8 -*-
"""
CNU Library Book Crawler
Playwright 기반 도서 검색, 대출 확인, 픽업 서비스 신청

사용법:
    from book_crawler import BookCrawler
    
    async with BookCrawler() as crawler:
        # 도서 검색
        results = await crawler.search_book("파이썬")
        
        # 로그인 후 픽업 신청
        await crawler.login("학번", "비밀번호")
        await crawler.request_pickup(book_id)
"""

import asyncio
import re
import urllib.parse
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

# ----------------------------
# 설정
# ----------------------------
BASE_URL = "https://library.cnu.ac.kr"
# 통합검색 URL: /searchTotal/result?st=KWRD&si=TOTAL&q=검색어
SEARCH_URL = f"{BASE_URL}/searchTotal/result"
# 상세보기 URL: /search/detail/CATTOT000000711410
DETAIL_URL = f"{BASE_URL}/search/detail"
LOGIN_URL = f"{BASE_URL}/login"


@dataclass
class Book:
    """도서 정보"""
    title: str
    author: str
    publisher: str
    year: str
    call_number: str  # 청구기호
    location: str  # 소장위치
    status: str  # 대출상태 (대출가능, 대출중, 예약중 등)
    return_date: str  # 반납예정일 (대출중인 경우)
    book_id: str  # 도서 ID
    detail_url: str  # 상세 페이지 URL
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @property
    def is_available(self) -> bool:
        return "대출가능" in self.status


@dataclass
class SearchResult:
    """검색 결과"""
    query: str
    total_count: int
    books: List[Book]
    success: bool
    message: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "query": self.query,
            "total_count": self.total_count,
            "books": [b.to_dict() for b in self.books],
            "success": self.success,
            "message": self.message
        }


class BookCrawler:
    """충남대 도서관 도서 크롤러"""
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_logged_in: bool = False
        self.playwright = None
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def start(self):
        """브라우저 시작"""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="ko-KR"
        )
        self.page = await self.context.new_page()
    
    async def close(self):
        """브라우저 종료"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
    
    async def login(self, user_id: str, password: str) -> Dict[str, Any]:
        """
        도서관 로그인
        
        Args:
            user_id: 학번 또는 교직원번호
            password: 충남대 포털 비밀번호
        
        Returns:
            로그인 결과
        """
        try:
            print(f"[DEBUG] 로그인 시도: {user_id}")
            await self.page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(1)
            
            print(f"[DEBUG] 로그인 페이지 URL: {self.page.url}")
            
            # 로그인 폼 입력
            # 아이디: <input id="id" name="id" ...>
            # 비밀번호: <input type="password" name="password" ...>
            
            await self.page.fill('input#id', user_id)
            await asyncio.sleep(0.3)
            
            await self.page.fill('input[name="password"]', password)
            await asyncio.sleep(0.3)
            
            print(f"[DEBUG] 폼 입력 완료")
            
            # 로그인 버튼 클릭 (다양한 셀렉터 시도)
            login_btn_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                '.btn-login',
                '.login-btn',
                'button:has-text("로그인")',
                'a:has-text("로그인")',
                '.btnLogin',
                '#loginBtn'
            ]
            
            clicked = False
            for sel in login_btn_selectors:
                try:
                    btn = await self.page.query_selector(sel)
                    if btn:
                        await btn.click()
                        clicked = True
                        print(f"[DEBUG] 로그인 버튼 클릭: {sel}")
                        break
                except:
                    continue
            
            if not clicked:
                # Enter 키로 로그인 시도
                await self.page.press('input[name="password"]', 'Enter')
                print(f"[DEBUG] Enter 키로 로그인 시도")
            
            # 로그인 결과 확인 (페이지 이동 대기)
            await self.page.wait_for_load_state("networkidle", timeout=10000)
            await asyncio.sleep(2)
            
            # 로그인 성공 여부 확인
            current_url = self.page.url
            page_content = await self.page.content()
            
            print(f"[DEBUG] 로그인 후 URL: {current_url}")
            
            # 성공 조건: 로그인 페이지가 아니고, 로그아웃 버튼이 있거나 마이페이지 링크가 있음
            if "login" not in current_url.lower():
                if "로그아웃" in page_content or "logout" in page_content.lower() or "마이페이지" in page_content or "내정보" in page_content:
                    self.is_logged_in = True
                    print(f"[DEBUG] 로그인 성공!")
                    return {
                        "success": True,
                        "message": "로그인 성공",
                        "user_id": user_id
                    }
            
            # 로그인 실패 원인 파악
            error_msg = "로그인 실패"
            error_selectors = ['.error', '.alert', '.message', '.login-error', '.err-msg']
            for sel in error_selectors:
                err_elem = await self.page.query_selector(sel)
                if err_elem:
                    err_text = await err_elem.text_content()
                    if err_text.strip():
                        error_msg = err_text.strip()
                        break
            
            print(f"[DEBUG] 로그인 실패: {error_msg}")
            return {
                "success": False,
                "message": f"로그인 실패 - {error_msg}"
            }
                
        except Exception as e:
            print(f"[DEBUG] 로그인 오류: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "message": f"로그인 오류: {str(e)}"
            }
    
    async def search_book(self, query: str, max_results: int = 10) -> SearchResult:
        """
        도서 검색
        
        Args:
            query: 검색어 (도서명, 저자명 등)
            max_results: 최대 결과 수
        
        Returns:
            SearchResult 객체
        """
        try:
            # 통합검색 URL: /searchTotal/result?st=KWRD&si=TOTAL&q=검색어
            encoded_query = urllib.parse.quote(query)
            search_url = f"{SEARCH_URL}?st=KWRD&si=TOTAL&q={encoded_query}"
            
            print(f"[DEBUG] 검색 URL: {search_url}")
            
            await self.page.goto(search_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)  # 결과 로딩 대기
            
            print(f"[DEBUG] 현재 URL: {self.page.url}")
            
            # 검색 결과 수 확인
            total_count = 0
            try:
                # 다양한 셀렉터 시도
                count_selectors = [
                    '.result-count', '.search-count', '.total-count',
                    '.result_count', '#totalCount', '.resultCount',
                    'span.count', '.search-result-count'
                ]
                for sel in count_selectors:
                    count_elem = await self.page.query_selector(sel)
                    if count_elem:
                        count_text = await count_elem.text_content()
                        count_match = re.search(r'(\d[\d,]*)', count_text.replace(',', ''))
                        if count_match:
                            total_count = int(count_match.group(1).replace(',', ''))
                            print(f"[DEBUG] 검색 결과 수: {total_count} (selector: {sel})")
                            break
            except Exception as e:
                print(f"[DEBUG] 결과 수 파싱 오류: {e}")
            
            # 도서 목록 파싱
            books = []
            
            # 다양한 도서 목록 셀렉터 시도
            item_selectors = [
                '.result-list li',
                '.search-result-item',
                '.book-item',
                '.list-item',
                'ul.list li',
                '.resultList li',
                '.search-list > li',
                '.data-list li',
                'div.result > ul > li',
                '.searchResultList li',
                'article.result',
                '.book-list li'
            ]
            
            book_items = []
            used_selector = None
            
            for sel in item_selectors:
                items = await self.page.query_selector_all(sel)
                if items and len(items) > 0:
                    book_items = items
                    used_selector = sel
                    print(f"[DEBUG] 도서 목록 발견: {len(items)}개 (selector: {sel})")
                    break
            
            if not book_items:
                # 페이지 HTML에서 도서 관련 링크 직접 탐색
                print("[DEBUG] 기본 셀렉터로 찾지 못함. 링크로 탐색 시도...")
                links = await self.page.query_selector_all('a[href*="/search/detail/"]')
                print(f"[DEBUG] 상세보기 링크 발견: {len(links)}개")
                
                for link in links[:max_results]:
                    try:
                        href = await link.get_attribute('href')
                        title_text = await link.text_content()
                        
                        if title_text and href:
                            # Book ID 추출: /search/detail/CATTOT000000711410
                            book_id = href.split('/detail/')[-1] if '/detail/' in href else ""
                            
                            # 부모 요소에서 추가 정보 추출 시도
                            parent = await link.evaluate_handle('el => el.closest("li") || el.parentElement')
                            author = ""
                            publisher = ""
                            status = "정보없음"
                            
                            if parent:
                                parent_text = await parent.evaluate('el => el.innerText')
                                # 간단한 파싱 (실제 구조에 맞게 조정 필요)
                                lines = parent_text.split('\n')
                                for line in lines:
                                    if '저자' in line or '지음' in line:
                                        author = line.strip()
                                    if '출판' in line:
                                        publisher = line.strip()
                                    if '대출가능' in line:
                                        status = "대출가능"
                                    elif '대출중' in line:
                                        status = "대출중"
                            
                            book = Book(
                                title=title_text.strip(),
                                author=author,
                                publisher=publisher,
                                year="",
                                call_number="",
                                location="",
                                status=status,
                                return_date="",
                                book_id=book_id,
                                detail_url=f"{BASE_URL}{href}" if not href.startswith('http') else href
                            )
                            books.append(book)
                            print(f"[DEBUG] 도서 추가: {book.title[:30]}... (ID: {book_id})")
                    except Exception as e:
                        print(f"[DEBUG] 링크 파싱 오류: {e}")
                        continue
            else:
                # 기존 셀렉터로 찾은 경우
                for item in book_items[:max_results]:
                    try:
                        book = await self._parse_book_item(item)
                        if book:
                            books.append(book)
                    except Exception as e:
                        print(f"[DEBUG] 도서 파싱 오류: {e}")
                        continue
            
            return SearchResult(
                query=query,
                total_count=total_count if total_count > 0 else len(books),
                books=books,
                success=len(books) > 0,
                message=f"{len(books)}건의 도서를 찾았습니다." if books else "검색 결과가 없습니다."
            )
            
        except Exception as e:
            print(f"[DEBUG] 검색 오류: {e}")
            import traceback
            traceback.print_exc()
            return SearchResult(
                query=query,
                total_count=0,
                books=[],
                success=False,
                message=f"검색 오류: {str(e)}"
            )
    
    async def _parse_book_item(self, item) -> Optional[Book]:
        """도서 항목 파싱"""
        try:
            # 제목
            title = ""
            title_elem = await item.query_selector('.title a, .book-title, td.title a, a.title')
            if title_elem:
                title = (await title_elem.text_content()).strip()
            
            if not title:
                return None
            
            # 상세 URL
            detail_url = ""
            if title_elem:
                href = await title_elem.get_attribute('href')
                if href:
                    detail_url = href if href.startswith('http') else f"{BASE_URL}{href}"
            
            # Book ID 추출
            book_id = ""
            if detail_url:
                id_match = re.search(r'recKey=(\d+)|id=(\d+)|book_id=(\d+)', detail_url)
                if id_match:
                    book_id = id_match.group(1) or id_match.group(2) or id_match.group(3)
            
            # 저자
            author = ""
            author_elem = await item.query_selector('.author, .book-author, td.author')
            if author_elem:
                author = (await author_elem.text_content()).strip()
            
            # 출판사
            publisher = ""
            pub_elem = await item.query_selector('.publisher, .book-publisher, td.publisher')
            if pub_elem:
                publisher = (await pub_elem.text_content()).strip()
            
            # 출판년도
            year = ""
            year_elem = await item.query_selector('.year, .pub-year, td.year')
            if year_elem:
                year = (await year_elem.text_content()).strip()
            
            # 청구기호
            call_number = ""
            call_elem = await item.query_selector('.call-number, .callno, td.callno')
            if call_elem:
                call_number = (await call_elem.text_content()).strip()
            
            # 소장위치
            location = ""
            loc_elem = await item.query_selector('.location, .lib-name, td.location')
            if loc_elem:
                location = (await loc_elem.text_content()).strip()
            
            # 대출상태
            status = "정보없음"
            return_date = ""
            status_elem = await item.query_selector('.status, .loan-status, td.status, .availability')
            if status_elem:
                status_text = (await status_elem.text_content()).strip()
                status = status_text
                
                # 반납예정일 추출
                date_match = re.search(r'(\d{4}[-/.]\d{2}[-/.]\d{2}|\d{2}[-/.]\d{2})', status_text)
                if date_match:
                    return_date = date_match.group(1)
            
            return Book(
                title=title,
                author=author,
                publisher=publisher,
                year=year,
                call_number=call_number,
                location=location,
                status=status,
                return_date=return_date,
                book_id=book_id,
                detail_url=detail_url
            )
            
        except Exception as e:
            print(f"파싱 오류: {e}")
            return None
    
    async def get_book_detail(self, book_id: str) -> Dict[str, Any]:
        """
        도서 상세 정보 조회
        
        Args:
            book_id: 도서 ID (예: CATTOT000000711410)
        
        Returns:
            도서 상세 정보
        """
        try:
            # 상세보기 URL: /search/detail/CATTOT000000711410
            detail_url = f"{DETAIL_URL}/{book_id}"
            print(f"[DEBUG] 상세 URL: {detail_url}")
            
            await self.page.goto(detail_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(1)
            
            # 상세 정보 파싱
            info = {
                "book_id": book_id,
                "detail_url": detail_url,
                "success": True
            }
            
            # 제목
            title_selectors = ['.book-title', '.detail-title', 'h1.title', 'h2.title', '.tit', '.book-name']
            for sel in title_selectors:
                title_elem = await self.page.query_selector(sel)
                if title_elem:
                    info["title"] = (await title_elem.text_content()).strip()
                    break
            
            # 소장 정보 테이블 파싱
            holdings = []
            
            # 테이블 행 찾기
            table_selectors = [
                '.holding-table tr',
                '.location-table tbody tr', 
                'table.holdings tr',
                '.book-info table tr',
                'table tr'
            ]
            
            for sel in table_selectors:
                rows = await self.page.query_selector_all(sel)
                if rows and len(rows) > 1:  # 헤더 제외
                    for row in rows[1:]:  # 첫 행(헤더) 제외
                        cols = await row.query_selector_all('td')
                        if len(cols) >= 2:
                            holding = {
                                "location": (await cols[0].text_content()).strip() if len(cols) > 0 else "",
                                "call_number": (await cols[1].text_content()).strip() if len(cols) > 1 else "",
                                "status": (await cols[-1].text_content()).strip() if cols else ""
                            }
                            holdings.append(holding)
                    if holdings:
                        print(f"[DEBUG] 소장 정보 {len(holdings)}건 발견")
                        break
            
            info["holdings"] = holdings
            
            # 대출 가능 여부
            page_text = await self.page.content()
            info["is_available"] = "대출가능" in page_text
            
            return info
            
        except Exception as e:
            print(f"[DEBUG] 상세 정보 조회 오류: {e}")
            return {
                "book_id": book_id,
                "success": False,
                "message": f"상세 정보 조회 오류: {str(e)}"
            }
    
    async def check_availability(self, book_id: str) -> Dict[str, Any]:
        """
        도서 대출 가능 여부 확인
        
        Args:
            book_id: 도서 ID
        
        Returns:
            대출 가능 여부 정보
        """
        try:
            detail_url = f"{DETAIL_URL}/{book_id}"
            print(f"[DEBUG] 상세 URL: {detail_url}")
            
            await self.page.goto(detail_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(1)
            
            # 제목 가져오기
            title = ""
            title_selectors = ['.book-title', '.detail-title', 'h1.title', 'h2.title', '.tit', '.book-name', '.titleArea']
            for sel in title_selectors:
                title_elem = await self.page.query_selector(sel)
                if title_elem:
                    title = (await title_elem.text_content()).strip()
                    break
            
            # 소장 정보에서 대출 상태 확인
            # <span class="status available">대출가능</span>
            available_copies = []
            unavailable_copies = []
            
            # 대출가능 상태 찾기
            available_elems = await self.page.query_selector_all('span.status.available, span.available, .status:has-text("대출가능")')
            print(f"[DEBUG] 대출가능 span 발견: {len(available_elems)}개")
            
            for elem in available_elems:
                text = await elem.text_content()
                if "대출가능" in text:
                    # 부모 행에서 위치 정보 추출 시도
                    parent_row = await elem.evaluate_handle('el => el.closest("tr") || el.closest("li") || el.parentElement')
                    location = ""
                    call_number = ""
                    
                    if parent_row:
                        try:
                            row_text = await parent_row.evaluate('el => el.innerText')
                            location = row_text.split('\n')[0] if row_text else ""
                        except:
                            pass
                    
                    available_copies.append({
                        "location": location.strip(),
                        "call_number": call_number,
                        "status": "대출가능"
                    })
            
            # 대출중 상태 찾기
            unavailable_elems = await self.page.query_selector_all('span.status.onloan, span.status.unavailable, .status:has-text("대출중")')
            print(f"[DEBUG] 대출중 span 발견: {len(unavailable_elems)}개")
            
            for elem in unavailable_elems:
                text = await elem.text_content()
                if "대출중" in text or "대출불가" in text:
                    parent_row = await elem.evaluate_handle('el => el.closest("tr") || el.closest("li") || el.parentElement')
                    location = ""
                    
                    if parent_row:
                        try:
                            row_text = await parent_row.evaluate('el => el.innerText')
                            location = row_text.split('\n')[0] if row_text else ""
                        except:
                            pass
                    
                    unavailable_copies.append({
                        "location": location.strip(),
                        "call_number": "",
                        "status": text.strip()
                    })
            
            total_count = len(available_copies) + len(unavailable_copies)
            
            # 만약 위 방법으로 못 찾았으면 페이지 텍스트에서 확인
            if total_count == 0:
                page_content = await self.page.content()
                if "대출가능" in page_content:
                    # 대출가능 텍스트 개수 세기
                    import re
                    available_count = len(re.findall(r'대출가능', page_content))
                    for _ in range(available_count):
                        available_copies.append({"location": "", "call_number": "", "status": "대출가능"})
                    print(f"[DEBUG] 텍스트 검색으로 대출가능 {available_count}개 발견")
            
            print(f"[DEBUG] 최종 - 대출가능: {len(available_copies)}개, 대출중: {len(unavailable_copies)}개")
            
            return {
                "book_id": book_id,
                "title": title,
                "is_available": len(available_copies) > 0,
                "available_count": len(available_copies),
                "total_count": len(available_copies) + len(unavailable_copies),
                "available_copies": available_copies,
                "unavailable_copies": unavailable_copies,
                "success": True
            }
            
        except Exception as e:
            print(f"[DEBUG] 대출 가능 확인 오류: {e}")
            import traceback
            traceback.print_exc()
            return {
                "book_id": book_id,
                "success": False,
                "message": f"대출 가능 확인 오류: {str(e)}"
            }
    
    async def request_pickup(self, book_id: str, pickup_location: str = "농학도서관") -> Dict[str, Any]:
        """
        분관대출 서비스 신청
        (도서 Pick-Up 서비스는 교원 전용이므로 분관대출 사용)
        
        Args:
            book_id: 도서 ID (예: CATTOT000000711410)
            pickup_location: 수령 희망 분관 (농학도서관, 법학도서관, 의학도서관)
        
        Returns:
            신청 결과
        """
        if not self.is_logged_in:
            return {
                "success": False,
                "message": "로그인이 필요합니다. login() 메서드를 먼저 호출하세요."
            }
        
        # 분관 코드 매핑
        branch_codes = {
            "농학도서관": "AL000000",
            "법학도서관": "LL000000", 
            "의학도서관": "ML000000"
        }
        
        # 입력값 정규화
        location_normalized = pickup_location.strip()
        if location_normalized not in branch_codes:
            # 부분 매칭 시도
            for name in branch_codes.keys():
                if location_normalized in name or name in location_normalized:
                    location_normalized = name
                    break
            else:
                return {
                    "success": False,
                    "message": f"지원하지 않는 분관입니다. 선택 가능: {', '.join(branch_codes.keys())}"
                }
        
        branch_code = branch_codes[location_normalized]
        
        try:
            # 도서 상세 페이지로 이동
            detail_url = f"{DETAIL_URL}/{book_id}"
            print(f"[DEBUG] 상세 페이지 이동: {detail_url}")
            
            await self.page.goto(detail_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            
            # 대출 가능 여부 확인
            page_content = await self.page.content()
            if "대출가능" not in page_content:
                return {
                    "success": False,
                    "message": "현재 대출 가능한 도서가 없습니다."
                }
            
            # 분관대출 링크 찾기
            # <a href="/search/branch/form?ctrl=...&accno=...&location=...&site_location=..." onclick="return doBranch(this)">
            branch_link = await self.page.query_selector('a[href*="/search/branch/form"]')
            
            if not branch_link:
                # 대안: 이미지로 찾기
                branch_link = await self.page.query_selector('a:has(img[alt*="분관대출"]), a:has(img[title*="분관대출"])')
            
            if not branch_link:
                return {
                    "success": False,
                    "message": "분관대출 버튼을 찾을 수 없습니다. 해당 도서는 분관대출을 지원하지 않을 수 있습니다."
                }
            
            # 분관대출 링크 URL 가져오기
            branch_href = await branch_link.get_attribute('href')
            print(f"[DEBUG] 분관대출 링크 발견: {branch_href}")
            
            # 분관대출 신청 페이지로 이동
            branch_url = f"{BASE_URL}{branch_href}" if not branch_href.startswith('http') else branch_href
            print(f"[DEBUG] 분관대출 신청 페이지 이동: {branch_url}")
            
            await self.page.goto(branch_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            
            print(f"[DEBUG] 현재 URL: {self.page.url}")
            
            # 분관 선택
            # <select id="receiveLoc" name="receiveLoc">
            location_select = await self.page.query_selector('select#receiveLoc, select[name="receiveLoc"]')
            
            if location_select:
                await location_select.select_option(value=branch_code)
                print(f"[DEBUG] 분관 선택: {location_normalized} ({branch_code})")
                await asyncio.sleep(0.5)
            else:
                print(f"[DEBUG] 분관 선택 드롭다운을 찾을 수 없음")
            
            # 신청 버튼 클릭
            submit_selectors = [
                '#submitButton',
                'a#submitButton',
                'a[title="신청"]',
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("신청")',
                'a:has-text("신청")',
                '.btn-submit',
                '.submitBtn'
            ]
            
            submitted = False
            for sel in submit_selectors:
                try:
                    btn = await self.page.query_selector(sel)
                    if btn:
                        await btn.click()
                        submitted = True
                        print(f"[DEBUG] 신청 버튼 클릭: {sel}")
                        break
                except:
                    continue
            
            if not submitted:
                return {
                    "success": False,
                    "message": "신청 버튼을 찾을 수 없습니다. 도서관 홈페이지에서 직접 신청해주세요.",
                    "branch_url": branch_url
                }
            
            # 결과 확인 대기
            await asyncio.sleep(2)
            
            # alert 창 처리 (있는 경우)
            try:
                dialog = await self.page.wait_for_event('dialog', timeout=3000)
                dialog_message = dialog.message
                print(f"[DEBUG] Alert 메시지: {dialog_message}")
                await dialog.accept()
                
                if "완료" in dialog_message or "신청" in dialog_message or "성공" in dialog_message:
                    return {
                        "success": True,
                        "message": f"분관대출 신청이 완료되었습니다! 수령 장소: {location_normalized}",
                        "book_id": book_id,
                        "pickup_location": location_normalized
                    }
                elif "실패" in dialog_message or "오류" in dialog_message or "불가" in dialog_message:
                    return {
                        "success": False,
                        "message": f"분관대출 신청 실패: {dialog_message}"
                    }
            except:
                # alert 없음
                pass
            
            await self.page.wait_for_load_state("networkidle", timeout=10000)
            
            # 결과 페이지 확인
            result_content = await self.page.content()
            current_url = self.page.url
            
            print(f"[DEBUG] 신청 후 URL: {current_url}")
            
            # 성공 여부 판단
            success_keywords = ["완료", "성공", "신청되었습니다", "접수", "등록"]
            error_keywords = ["실패", "오류", "에러", "불가", "없습니다", "권한"]
            
            is_success = any(kw in result_content for kw in success_keywords)
            is_error = any(kw in result_content for kw in error_keywords)
            
            if is_success and not is_error:
                return {
                    "success": True,
                    "message": f"분관대출 신청이 완료되었습니다! 수령 장소: {location_normalized}",
                    "book_id": book_id,
                    "pickup_location": location_normalized
                }
            elif is_error:
                error_elem = await self.page.query_selector('.error, .alert, .message, .err-msg')
                error_msg = ""
                if error_elem:
                    error_msg = await error_elem.text_content()
                return {
                    "success": False,
                    "message": f"분관대출 신청 실패: {error_msg}" if error_msg else "분관대출 신청에 실패했습니다."
                }
            else:
                return {
                    "success": True,
                    "message": f"분관대출 신청이 처리되었습니다. 수령 장소: {location_normalized}. 도서관 마이페이지에서 신청 현황을 확인해주세요.",
                    "book_id": book_id,
                    "pickup_location": location_normalized
                }
                
        except Exception as e:
            print(f"[DEBUG] 분관대출 신청 오류: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "message": f"분관대출 신청 오류: {str(e)}"
            }
    
    async def get_my_loans(self) -> Dict[str, Any]:
        """
        분관대출 현황 조회
        
        Returns:
            분관대출 신청 현황
        """
        if not self.is_logged_in:
            return {
                "success": False,
                "message": "로그인이 필요합니다."
            }
        
        try:
            # 분관대출 현황 페이지로 이동
            branch_loan_url = f"{BASE_URL}/myloan/branch"
            print(f"[DEBUG] 분관대출 현황 페이지 이동: {branch_loan_url}")
            
            await self.page.goto(branch_loan_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            
            print(f"[DEBUG] 현재 URL: {self.page.url}")
            
            loans = []
            
            # 테이블 행 찾기
            row_selectors = [
                'table tbody tr',
                '.loan-list tr',
                '.list-table tr',
                '.dataTable tr',
                'table.list tr'
            ]
            
            for sel in row_selectors:
                rows = await self.page.query_selector_all(sel)
                if rows and len(rows) > 0:
                    print(f"[DEBUG] 테이블 행 발견: {len(rows)}개 (selector: {sel})")
                    
                    for row in rows:
                        cols = await row.query_selector_all('td')
                        if len(cols) >= 2:
                            # 각 열에서 텍스트 추출
                            col_texts = []
                            for col in cols:
                                text = await col.text_content()
                                col_texts.append(text.strip() if text else "")
                            
                            # 도서 제목 찾기 (링크가 있는 열)
                            title = ""
                            title_link = await row.query_selector('a[href*="/search/detail"]')
                            if title_link:
                                title = (await title_link.text_content()).strip()
                            else:
                                # 첫 번째 또는 두 번째 열이 제목일 가능성
                                title = col_texts[0] if col_texts else ""
                            
                            if title and title != "":
                                loan = {
                                    "title": title,
                                    "request_date": col_texts[1] if len(col_texts) > 1 else "",
                                    "receive_location": col_texts[2] if len(col_texts) > 2 else "",
                                    "status": col_texts[-1] if col_texts else "",
                                    "raw_data": col_texts
                                }
                                loans.append(loan)
                                print(f"[DEBUG] 분관대출 항목: {title[:30]}...")
                    
                    if loans:
                        break
            
            # 데이터가 없는 경우 페이지 텍스트 확인
            if not loans:
                page_content = await self.page.content()
                if "신청내역이 없습니다" in page_content or "데이터가 없습니다" in page_content:
                    return {
                        "success": True,
                        "count": 0,
                        "loans": [],
                        "message": "분관대출 신청 내역이 없습니다."
                    }
            
            return {
                "success": True,
                "count": len(loans),
                "loans": loans,
                "message": f"분관대출 {len(loans)}건 조회됨"
            }
            
        except Exception as e:
            print(f"[DEBUG] 분관대출 현황 조회 오류: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "message": f"분관대출 현황 조회 오류: {str(e)}"
            }


# ----------------------------
# 동기 래퍼 함수들 (MCP Tool용)
# ----------------------------
_crawler_instance: Optional[BookCrawler] = None


async def _get_crawler() -> BookCrawler:
    """크롤러 인스턴스 가져오기 (싱글톤)"""
    global _crawler_instance
    if _crawler_instance is None:
        _crawler_instance = BookCrawler(headless=True)
        await _crawler_instance.start()
    return _crawler_instance


async def search_book_async(query: str, max_results: int = 10) -> Dict[str, Any]:
    """도서 검색 (async)"""
    crawler = await _get_crawler()
    result = await crawler.search_book(query, max_results)
    return result.to_dict()


async def check_book_availability_async(book_id: str) -> Dict[str, Any]:
    """대출 가능 여부 확인 (async)"""
    crawler = await _get_crawler()
    return await crawler.check_availability(book_id)


async def login_async(user_id: str, password: str) -> Dict[str, Any]:
    """로그인 (async)"""
    crawler = await _get_crawler()
    return await crawler.login(user_id, password)


async def request_pickup_async(book_id: str, pickup_location: str = "중앙도서관") -> Dict[str, Any]:
    """픽업 신청 (async)"""
    crawler = await _get_crawler()
    return await crawler.request_pickup(book_id, pickup_location)


async def get_my_loans_async() -> Dict[str, Any]:
    """내 대출 현황 (async)"""
    crawler = await _get_crawler()
    return await crawler.get_my_loans()


# ----------------------------
# CLI 테스트
# ----------------------------
async def main():
    """CLI 테스트"""
    print("=" * 60)
    print("CNU Library Book Crawler - Test")
    print("=" * 60)
    
    async with BookCrawler(headless=False) as crawler:
        while True:
            print("\n명령어:")
            print("  1. 도서 검색")
            print("  2. 대출 가능 확인")
            print("  3. 로그인")
            print("  4. 분관대출 신청")
            print("  5. 분관대출 현황")
            print("  q. 종료")
            
            cmd = input("\n선택> ").strip()
            
            if cmd == "q":
                break
            elif cmd == "1":
                query = input("검색어: ")
                result = await crawler.search_book(query)
                print(f"\n검색 결과: {result.message}")
                for i, book in enumerate(result.books, 1):
                    print(f"  {i}. {book.title} / {book.author} / {book.status}")
            elif cmd == "2":
                book_id = input("도서 ID: ")
                result = await crawler.check_availability(book_id)
                print(f"\n대출 가능: {result.get('is_available')}")
                print(f"가능 수량: {result.get('available_count')}/{result.get('total_count')}")
            elif cmd == "3":
                user_id = input("학번: ")
                password = input("비밀번호: ")
                result = await crawler.login(user_id, password)
                print(f"\n결과: {result.get('message')}")
            elif cmd == "4":
                book_id = input("도서 ID: ")
                print("분관 선택:")
                print("  1. 농학도서관")
                print("  2. 법학도서관")
                print("  3. 의학도서관")
                branch_choice = input("선택 (1/2/3, 기본:1): ").strip() or "1"
                branch_map = {"1": "농학도서관", "2": "법학도서관", "3": "의학도서관"}
                location = branch_map.get(branch_choice, "농학도서관")
                result = await crawler.request_pickup(book_id, location)
                print(f"\n결과: {result.get('message')}")
            elif cmd == "5":
                result = await crawler.get_my_loans()
                print(f"\n분관대출 현황: {result.get('count', 0)}건")
                for loan in result.get("loans", []):
                    print(f"  - {loan.get('title', '')[:30]} | 수령처: {loan.get('receive_location', '')} | 상태: {loan.get('status', '')}")


if __name__ == "__main__":
    asyncio.run(main())