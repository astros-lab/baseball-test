"""
야구선수 데이터 스크래퍼
Korea Baseball Softball Association 웹사이트에서 선수 정보를 가져옵니다.
"""

import requests
from bs4 import BeautifulSoup
import json
import pandas as pd

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    import time
    SELENIUM_AVAILABLE = True
    
    # webdriver-manager 사용 시도 (자동 드라이버 다운로드)
    try:
        from selenium.webdriver.chrome.service import Service as ChromeService
        from webdriver_manager.chrome import ChromeDriverManager
        USE_WEBDRIVER_MANAGER = True
    except ImportError:
        USE_WEBDRIVER_MANAGER = False
except ImportError:
    SELENIUM_AVAILABLE = False
    USE_WEBDRIVER_MANAGER = False
    print("Selenium이 설치되지 않았습니다. use_selenium=True를 사용하려면 'pip install selenium webdriver-manager'를 실행하세요.")


SECTION_ALIASES = {
    '2025 시즌': '2025_시즌',
    '최근 5 경기': '최근_5경기',
    '대회별기록': '대회별_기록',
    '연도별기록': '연도별_기록',
    '출신학교': '출신학교',
    '수상내역': '수상내역',
}


def normalize_text(text):
    """공백을 정리한 비교용 문자열 반환"""
    return ' '.join(str(text).split()) if text else ''


def extract_table_rows(table):
    """테이블에서 헤더와 행 데이터를 최대한 유연하게 추출"""
    headers = []
    rows = table.find_all('tr')

    # 헤더 찾기
    for row in rows:
        ths = row.find_all('th')
        if ths:
            headers = [normalize_text(th.get_text(' ', strip=True)) for th in ths]
            if headers:
                break

    if not headers and rows:
        first_cells = rows[0].find_all(['td', 'th'])
        headers = [normalize_text(cell.get_text(' ', strip=True)) for cell in first_cells]

    # 데이터 행 추출
    table_data = []
    header_found = False
    
    for row in rows:
        # th가 있는 행은 헤더 행으로 간주하고 건너뜀
        if row.find_all('th'):
            header_found = True
            continue
            
        cells = row.find_all('td')
        if not cells:
            continue

        # 각 셀의 텍스트 추출
        values = []
        for cell in cells:
            cell_text = normalize_text(cell.get_text(' ', strip=True))
            values.append(cell_text)
        
        # 빈 행 건너뛰기
        if not any(values):
            continue
        
        # 헤더와 값의 개수가 맞으면 딕셔너리로 변환
        if headers and len(headers) == len(values):
            row_data = {headers[i]: values[i] for i in range(len(values))}
        else:
            # 개수가 안 맞으면 col_1, col_2... 형태로 저장
            row_data = {f'col_{i + 1}': value for i, value in enumerate(values)}
        
        if any(row_data.values()):
            table_data.append(row_data)

    return headers, table_data


def extract_ul_list_rows(ul_element):
    """<ul> 안의 <li><span> 구조를 테이블처럼 파싱"""
    headers = []
    table_data = []
    
    all_li = ul_element.find_all('li', recursive=False)
    
    # 첫 번째 li class="title"을 헤더로 사용
    for li in all_li:
        if 'title' in li.get('class', []):
            spans = li.find_all('span', class_='sort') + li.find_all('span', class_='sort_comp')
            headers = [normalize_text(span.get_text(' ', strip=True)) for span in spans]
            break
    
    if not headers:
        return table_data  # 헤더가 없으면 빈 리스트 반환
    
    # 나머지 li를 데이터 행으로 처리
    for li in all_li:
        if 'title' in li.get('class', []):
            continue
        
        # 모든 span 추출 (순서대로)
        spans = li.find_all('span')
        values = []
        for span in spans:
            # <a> 태그가 있으면 링크 텍스트 사용
            link = span.find('a')
            if link:
                values.append(normalize_text(link.get_text(' ', strip=True)))
            else:
                values.append(normalize_text(span.get_text(' ', strip=True)))
        
        # 값 개수가 헤더보다 많으면 자르기
        if len(values) > len(headers):
            values = values[:len(headers)]
        
        if headers and len(headers) == len(values):
            row_data = {headers[i]: values[i] for i in range(len(values))}
            if any(row_data.values()):
                table_data.append(row_data)
    
    return table_data  # 리스트만 반환 (헤더는 딕셔너리 키로 포함됨)


def find_section_data_block(section_heading):
    """섹션 제목 다음에 오는 데이터 블록(table/div/ul/dl 등)을 탐색"""
    current = section_heading
    while current:
        current = current.find_next_sibling()
        if not current:
            return None
        # 다음 섹션 제목이 나오면 중단
        if current.name in ['h3', 'h4', 'h5']:
            return None
        # 의미 없는 빈 태그 건너뛰기
        if getattr(current, 'name', None) in ['br', 'hr'] or (getattr(current, 'text', '').strip() == ''):
            continue
        # 표
        if current.name == 'table':
            return current
        # 리스트
        if current.name in ['ul', 'ol', 'dl']:
            return current
        # div 내부에 표/리스트/정의목록/텍스트가 있으면 반환
        if current.name == 'div':
            if current.find('table'):
                return current.find('table')
            if current.find(['ul', 'ol', 'dl']):
                return current.find(['ul', 'ol', 'dl'])
            # div 내부 텍스트가 의미 있으면 반환
            if current.get_text(strip=True):
                return current
        # 기타 텍스트 블록
        if current.get_text(strip=True):
            return current
    return None

def get_player_data_selenium(person_no, gubun='P', debug=False):
    """
    Selenium을 사용하여 선수 정보를 가져오는 함수 (JavaScript 처리)
    
    Parameters:
    - person_no: 선수 번호
    - gubun: 'P' (투수), 'P#hitter' (타자)
    - debug: True면 HTML을 파일로 저장
    
    Returns:
    - dict: 선수 정보 딕셔너리
    """
    if not SELENIUM_AVAILABLE:
        print("Selenium이 설치되지 않았습니다. requests 방식으로 대체합니다.")
        return get_player_data(person_no, gubun, debug, use_selenium=False)
    
    url = f'https://www.korea-baseball.com/info/player/player_view?person_no={person_no}&gubun={gubun}'
    
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
    
    # PATH의 구버전 chromedriver 무시하고 Selenium Manager 사용
    import os
    original_path = os.environ.get('PATH', '')
    
    driver = None
    try:
        # PATH에서 chromedriver 제거 (임시)
        path_parts = original_path.split(os.pathsep)
        filtered_paths = [p for p in path_parts if 'chromedriver' not in p.lower()]
        os.environ['PATH'] = os.pathsep.join(filtered_paths)
        
        # webdriver-manager를 사용하여 자동으로 호환되는 ChromeDriver 다운로드
        if USE_WEBDRIVER_MANAGER:
            print("webdriver-manager를 사용하여 ChromeDriver를 자동으로 설정합니다...")
            # ChromeDriverManager가 자동으로 최신 호환 버전을 다운로드
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
        else:
            # Selenium 4의 자동 드라이버 관리 사용
            print("Selenium의 자동 드라이버 관리를 사용합니다...")
            # Selenium Manager가 자동으로 호환되는 드라이버 다운로드
            driver = webdriver.Chrome(options=chrome_options)
        
        # PATH 복원
        os.environ['PATH'] = original_path
        
        print("ChromeDriver 초기화 성공!")
        driver.get(url)
        time.sleep(3)  # 페이지 로딩 및 JavaScript 실행 대기
        
        # #hitter가 URL에 있으면 타자 탭 클릭 시도
        if '#hitter' in gubun.lower():
            print("타자기록 탭을 찾는 중...")
            try:
                # 여러 방법으로 타자기록 탭 찾기
                hitter_tab = None
                
                # 방법 1: 링크 텍스트로 찾기
                try:
                    hitter_tab = driver.find_element(By.LINK_TEXT, "타자기록")
                    print("방법 1 성공: LINK_TEXT로 '타자기록' 찾음")
                except:
                    pass
                
                # 방법 2: 부분 텍스트로 찾기
                if not hitter_tab:
                    try:
                        hitter_tab = driver.find_element(By.PARTIAL_LINK_TEXT, "타자")
                        print("방법 2 성공: PARTIAL_LINK_TEXT로 '타자' 찾음")
                    except:
                        pass
                
                # 방법 3: XPath로 텍스트 포함 찾기
                if not hitter_tab:
                    try:
                        hitter_tab = driver.find_element(By.XPATH, "//a[contains(text(), '타자기록')]")
                        print("방법 3 성공: XPath로 '타자기록' 찾음")
                    except:
                        pass
                
                # 방법 4: 버튼/탭 요소 찾기
                if not hitter_tab:
                    try:
                        hitter_tab = driver.find_element(By.XPATH, "//*[contains(text(), '타자기록')]")
                        print("방법 4 성공: XPath로 모든 요소에서 '타자기록' 찾음")
                    except:
                        pass
                
                # 방법 5: href 속성으로 찾기
                if not hitter_tab:
                    try:
                        hitter_tab = driver.find_element(By.CSS_SELECTOR, "[href='#hitter']")
                        print("방법 5 성공: CSS_SELECTOR로 href='#hitter' 찾음")
                    except:
                        pass
                
                if hitter_tab:
                    print(f"타자기록 탭 찾음: {hitter_tab.text}")
                    hitter_tab.click()
                    time.sleep(5)  # 탭 전환 대기 시간 증가
                    print("타자기록 탭으로 전환 완료!")
                else:
                    print("❌ 타자기록 탭을 찾지 못했습니다. 페이지 HTML을 확인하세요.")
                    # 디버그: 페이지의 모든 링크 출력
                    links = driver.find_elements(By.TAG_NAME, "a")
                    print(f"페이지의 링크 목록 (처음 10개):")
                    for i, link in enumerate(links[:10]):
                        print(f"  {i+1}. {link.text} (href: {link.get_attribute('href')})")
                        
            except Exception as tab_error:
                print(f"❌ 타자기록 탭 클릭 오류: {tab_error}")
        
        # 디버그 모드: HTML 저장
        if debug:
            with open('debug_page_selenium.html', 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            print("HTML을 debug_page_selenium.html에 저장했습니다.")
        
        # HTML 파싱
        soup = BeautifulSoup(driver.page_source, 'lxml')
        
        # gubun에 따라 특정 섹션만 파싱
        if '#hitter' in gubun.lower():
            # 타자기록 섹션만 찾기
            content_section = soup.find('div', id='hitter') or soup.find('div', class_=lambda x: x and 'hitter' in x.lower())
            if content_section:
                print("✓ 타자기록 섹션을 찾았습니다.")
                soup = content_section  # soup을 타자 섹션으로 제한
            else:
                print("⚠ 타자기록 섹션을 찾지 못했습니다. 전체 페이지를 파싱합니다.")
        elif gubun == 'P' or '#pitcher' in gubun.lower():
            # 투수기록 섹션만 찾기
            content_section = soup.find('div', id='pitcher') or soup.find('div', class_=lambda x: x and 'pitcher' in x.lower())
            if content_section:
                print("✓ 투수기록 섹션을 찾았습니다.")
                soup = content_section  # soup을 투수 섹션으로 제한
            else:
                print("⚠ 투수기록 섹션을 찾지 못했습니다. 전체 페이지를 파싱합니다.")
        
        player_data = {
            'person_no': person_no,
            'url': url
        }
        
        # 선수 기본 정보 추출 (일반화)
        player_info_div = soup.find('div', class_='player_info')
        name_elem = None
        if player_info_div:
            name_elem = player_info_div.find('h2') or player_info_div.find('strong')
        if not name_elem:
            info_table = soup.find('table')
            if info_table:
                first_tr = info_table.find('tr')
                if first_tr:
                    first_td = first_tr.find('td')
                    if first_td:
                        name_elem = first_td
        if name_elem:
            player_data['이름'] = normalize_text(name_elem.get_text(' ', strip=True))
        
        # 테이블에서 기본 정보 추출
        info_table = soup.find('table')
        if info_table:
            rows = info_table.find_all('tr')
            for row in rows:
                cols = row.find_all('td')
                if len(cols) == 1:
                    text = cols[0].get_text(strip=True)
                    if text and text not in player_data.values():
                        if text.isdigit() and len(text) <= 3:
                            player_data['등번호'] = text
                        elif '년' in text or '세' in text:
                            player_data['생년월일'] = text
                        elif 'cm' in text or 'kg' in text:
                            player_data['신장/체중'] = text
                        elif '투' in text and '타' in text:
                            player_data['투타'] = text
                        elif not player_data.get('이름'):
                            player_data['이름'] = text
                        else:
                            player_data['포지션'] = text
        
        # 섹션별 데이터 추출 (기존 로직과 동일)
        all_records = {
            '2025_시즌': [],
            '최근_5경기': [],
            '대회별_기록': [],
            '연도별_기록': [],
            '출신학교': [],
            '수상내역': []
        }
        
        section_headings = soup.find_all(['h3', 'h4', 'h5'])
        print(f"총 {len(section_headings)}개의 섹션 제목을 찾았습니다.")
        
        # 디버그: 모든 섹션 제목 출력
        print("발견된 섹션 제목:")
        for heading in section_headings:
            title = normalize_text(heading.get_text(' ', strip=True))
            section_key = SECTION_ALIASES.get(title)
            print(f"  - '{title}' → {section_key if section_key else '(매칭 안됨)'}")

        for heading in section_headings:
            raw_title = heading.get_text(' ', strip=True)
            title = normalize_text(raw_title)
            section_key = SECTION_ALIASES.get(title)
            if not section_key:
                continue

            data_block = find_section_data_block(heading)
            if not data_block:
                print(f"섹션 '{title}': 연결된 데이터 블록을 찾지 못했습니다.")
                continue

            if data_block.name == 'table':
                headers, table_data = extract_table_rows(data_block)
                print(f"섹션 '{title}' 헤더: {headers[:5] if headers else '없음'}")
                if table_data:
                    print(f"  첫 번째 데이터 행: {table_data[0]}")
                    all_records[section_key] = table_data
                    print(f"섹션 '{title}': {len(table_data)}개 행 추출")
                else:
                    print(f"섹션 '{title}': 행 데이터를 추출하지 못했습니다.")
            # 리스트(ul/ol)인 경우 - 내부에 테이블이 있을 수 있음
            elif data_block.name in ['ul', 'ol']:
                # 리스트 내부에 테이블이 있는지 확인
                inner_table = data_block.find('table')
                if inner_table:
                    headers, table_data = extract_table_rows(inner_table)
                    print(f"섹션 '{title}' (리스트 내 테이블) 헤더: {headers[:5] if headers else '없음'}")
                    if table_data:
                        print(f"  첫 번째 데이터 행: {table_data[0]}")
                        all_records[section_key] = table_data
                        print(f"섹션 '{title}': {len(table_data)}개 행 추출")
                    else:
                        print(f"섹션 '{title}': 행 데이터를 추출하지 못했습니다.")
                else:
                    # 리스트 내부에 테이블이 없습니다. <ul><li><span> 구조를 파싱합니다.
                    print(f"리스트 내부에 테이블이 없습니다. <ul><li><span> 구조를 파싱합니다.")
                    ul_data = extract_ul_list_rows(data_block)
                    if ul_data:
                        all_records[section_key] = ul_data
                        print(f"섹션 '{title}': <ul> 구조에서 {len(ul_data)}개 행 추출")
                        print(f"  첫 번째 데이터 행: {ul_data[0]}")
                    else:
                        # 기존 fallback: 단순 텍스트 추출
                        items = [normalize_text(li.get_text(' ', strip=True)) for li in data_block.find_all('li')]
                        if items:
                            all_records[section_key] = [{'항목': item} for item in items]
                            print(f"섹션 '{title}': 리스트 {len(items)}개 항목 추출 (fallback)")
                        else:
                            print(f"섹션 '{title}': 리스트 항목을 추출하지 못했습니다.")
            elif data_block.name == 'dl':
                dts = data_block.find_all('dt')
                dds = data_block.find_all('dd')
                if dts and dds and len(dts) == len(dds):
                    all_records[section_key] = [
                        {normalize_text(dt.get_text(' ', strip=True)): normalize_text(dd.get_text(' ', strip=True))}
                        for dt, dd in zip(dts, dds)
                    ]
                    print(f"섹션 '{title}': 정의목록 {len(dts)}개 항목 추출")
            else:
                text = data_block.get_text(' ', strip=True)
                if text:
                    all_records[section_key] = [{'내용': text}]
                    print(f"섹션 '{title}': 텍스트 블록 추출")

        if not any(all_records.values()):
            print("섹션 기반 추출이 비어 있어 전체 테이블 백업 탐색을 시도합니다.")
            tables = soup.find_all('table')
            for idx, table in enumerate(tables):
                headers, table_data = extract_table_rows(table)
                if not table_data:
                    continue

                header_set = set(headers)
                if '경기일자' in header_set:
                    all_records['최근_5경기'] = table_data
                elif '대회명' in header_set and '경기수' in header_set:
                    all_records['대회별_기록'] = table_data
                elif '연도' in header_set and '소속' in header_set:
                    all_records['연도별_기록'] = table_data
                elif '수상명' in header_set:
                    all_records['수상내역'] = table_data
                elif '지역' in header_set:
                    all_records['출신학교'] = table_data
                elif '평균자책점' in header_set or '이닝' in header_set:
                    all_records['2025_시즌'] = table_data
                else:
                    all_records.setdefault(f'테이블_{idx}', table_data)
        
        player_data['records'] = all_records
        
        return player_data
        
    except Exception as e:
        print(f"Selenium 오류 발생: {e}")
        # PATH 복원 (에러 발생 시에도)
        import os
        if 'original_path' in locals():
            os.environ['PATH'] = original_path
        return None
    finally:
        if driver:
            driver.quit()

def get_player_data(person_no, gubun='P', debug=False, use_selenium=False):
    """
    선수 정보를 가져오는 함수
    
    Parameters:
    - person_no: 선수 번호 (예: '201508002605')
    - gubun: 'P' (투수), 'P#hitter' (타자)
    - debug: True면 HTML을 파일로 저장
    - use_selenium: True면 Selenium 사용 (동적 콘텐츠/JavaScript 처리)
    
    Returns:
    - dict: 선수 정보 딕셔너리
    """
    url = f'https://www.korea-baseball.com/info/player/player_view?person_no={person_no}&gubun={gubun}'
    
    if use_selenium:
        return get_player_data_selenium(person_no, gubun, debug)
    
    # 헤더 설정 (웹사이트가 봇을 차단하지 않도록)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        # 웹페이지 요청
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        # 디버그 모드: HTML 저장
        if debug:
            with open('debug_page.html', 'w', encoding='utf-8') as f:
                f.write(response.text)
            print("HTML을 debug_page.html에 저장했습니다.")
        
        # HTML 파싱
        soup = BeautifulSoup(response.text, 'lxml')
        original_soup = soup  # 원본 soup 보존 (출신학교, 수상내역용)
        
        # gubun에 따라 특정 섹션만 파싱
        if '#hitter' in gubun.lower():
            # 타자기록 섹션만 찾기
            content_section = soup.find('div', id='hitter') or soup.find('div', class_=lambda x: x and 'hitter' in x.lower())
            if content_section:
                print("✓ 타자기록 섹션을 찾았습니다.")
                soup = content_section  # soup을 타자 섹션으로 제한
            else:
                print("⚠ 타자기록 섹션을 찾지 못했습니다. 전체 페이지를 파싱합니다.")
        elif gubun == 'P' or '#pitcher' in gubun.lower():
            # 투수기록 섹션만 찾기
            content_section = soup.find('div', id='pitcher') or soup.find('div', class_=lambda x: x and 'pitcher' in x.lower())
            if content_section:
                print("✓ 투수기록 섹션을 찾았습니다.")
                soup = content_section  # soup을 투수 섹션으로 제한
            else:
                print("⚠ 투수기록 섹션을 찾지 못했습니다. 전체 페이지를 파싱합니다.")
        
        player_data = {
            'person_no': person_no,
            'url': url
        }
        
        # 선수 기본 정보 추출 (일반화)
        # 1. player_info div에서 h2/strong 등
        player_info_div = soup.find('div', class_='player_info')
        name_elem = None
        if player_info_div:
            name_elem = player_info_div.find('h2') or player_info_div.find('strong')
        # 2. 없으면, 첫 번째 테이블의 첫 번째 tr의 첫 번째 td
        if not name_elem:
            info_table = soup.find('table')
            if info_table:
                first_tr = info_table.find('tr')
                if first_tr:
                    first_td = first_tr.find('td')
                    if first_td:
                        name_elem = first_td
        # 이름 저장
        if name_elem:
            player_data['이름'] = normalize_text(name_elem.get_text(' ', strip=True))
        
        # 테이블에서 기본 정보 추출
        info_table = soup.find('table')
        if info_table:
            rows = info_table.find_all('tr')
            for row in rows:
                cols = row.find_all('td')
                if len(cols) == 1:
                    # 단일 컬럼 (이름, 번호 등)
                    text = cols[0].get_text(strip=True)
                    if text and text not in player_data.values():
                        # 키 유추
                        if text.isdigit() and len(text) <= 3:
                            player_data['등번호'] = text
                        elif '년' in text or '세' in text:
                            player_data['생년월일'] = text
                        elif 'cm' in text or 'kg' in text:
                            player_data['신장/체중'] = text
                        elif '투' in text and '타' in text:
                            player_data['투타'] = text
                        elif not player_data.get('이름'):
                            player_data['이름'] = text
                        else:
                            player_data['포지션'] = text
        
        # 2025 시즌 기록, 최근 경기, 대회별 기록 등 모든 섹션별 데이터 추출
        all_records = {
            '2025_시즌': [],
            '최근_5경기': [],
            '대회별_기록': [],
            '연도별_기록': [],
            '출신학교': [],
            '수상내역': []
        }
        
        section_headings = soup.find_all(['h3', 'h4', 'h5'])
        print(f"총 {len(section_headings)}개의 섹션 제목을 찾았습니다.")
        
        # 디버그: 모든 섹션 제목 출력
        print("발견된 섹션 제목:")
        for heading in section_headings:
            title = normalize_text(heading.get_text(' ', strip=True))
            section_key = SECTION_ALIASES.get(title)
            print(f"  - '{title}' → {section_key if section_key else '(매칭 안됨)'}")

        for heading in section_headings:
            raw_title = heading.get_text(' ', strip=True)
            title = normalize_text(raw_title)
            section_key = SECTION_ALIASES.get(title)
            if not section_key:
                continue


            data_block = find_section_data_block(heading)
            if not data_block:
                print(f"섹션 '{title}': 연결된 데이터 블록을 찾지 못했습니다.")
                continue

            # 표인 경우 기존 방식
            if data_block.name == 'table':
                headers, table_data = extract_table_rows(data_block)
                print(f"섹션 '{title}' 헤더: {headers[:5] if headers else '없음'}")
                # 디버그: 첫 번째 데이터 행 출력
                if table_data:
                    print(f"  첫 번째 데이터 행: {table_data[0]}")
                    all_records[section_key] = table_data
                    print(f"섹션 '{title}': {len(table_data)}개 행 추출")
                else:
                    print(f"섹션 '{title}': 행 데이터를 추출하지 못했습니다.")
            # 리스트(ul/ol)인 경우 - 내부에 테이블이 있을 수 있음
            elif data_block.name in ['ul', 'ol']:
                # 리스트 내부에 테이블이 있는지 확인
                inner_table = data_block.find('table')
                if inner_table:
                    headers, table_data = extract_table_rows(inner_table)
                    print(f"섹션 '{title}' (리스트 내 테이블) 헤더: {headers[:5] if headers else '없음'}")
                    if table_data:
                        print(f"  첫 번째 데이터 행: {table_data[0]}")
                        all_records[section_key] = table_data
                        print(f"섹션 '{title}': {len(table_data)}개 행 추출")
                    else:
                        print(f"섹션 '{title}': 행 데이터를 추출하지 못했습니다.")
                else:
                    # 리스트 내부에 테이블이 없습니다. <ul><li><span> 구조를 파싱합니다.
                    print(f"리스트 내부에 테이블이 없습니다. <ul><li><span> 구조를 파싱합니다.")
                    ul_data = extract_ul_list_rows(data_block)
                    if ul_data:
                        all_records[section_key] = ul_data
                        print(f"섹션 '{title}': <ul> 구조에서 {len(ul_data)}개 행 추출")
                        print(f"  첫 번째 데이터 행: {ul_data[0]}")
                    else:
                        # 기존 fallback: 단순 텍스트 추출
                        items = [normalize_text(li.get_text(' ', strip=True)) for li in data_block.find_all('li')]
                        if items:
                            all_records[section_key] = [{'항목': item} for item in items]
                            print(f"섹션 '{title}': 리스트 {len(items)}개 항목 추출 (fallback)")
                        else:
                            print(f"섹션 '{title}': 리스트 항목을 추출하지 못했습니다.")
            elif data_block.name == 'dl':
                dts = data_block.find_all('dt')
                dds = data_block.find_all('dd')
                if dts and dds and len(dts) == len(dds):
                    all_records[section_key] = [
                        {normalize_text(dt.get_text(' ', strip=True)): normalize_text(dd.get_text(' ', strip=True))}
                        for dt, dd in zip(dts, dds)
                    ]
                    print(f"섹션 '{title}': 정의목록 {len(dts)}개 항목 추출")
                else:
                    print(f"섹션 '{title}': 정의목록 항목을 추출하지 못했습니다.")
            # div/텍스트 블록인 경우
            else:
                text = data_block.get_text(' ', strip=True)
                if text:
                    all_records[section_key] = [{'내용': text}]
                    print(f"섹션 '{title}': 텍스트 블록 추출")
                else:
                    print(f"섹션 '{title}': 텍스트 블록이 비어 있습니다.")

        # 출신학교, 수상내역은 타자/투수 섹션 밖에 있으므로 original_soup에서 추가 검색
        common_sections = ['출신학교', '수상내역']
        for common_section in common_sections:
            if not all_records.get(common_section):  # 아직 추출 안 됐으면
                print(f"\n'{common_section}' 섹션을 전체 페이지에서 검색합니다...")
                for heading in original_soup.find_all(['h3', 'h4', 'h5']):
                    title = normalize_text(heading.get_text(' ', strip=True))
                    if title == common_section:
                        data_block = find_section_data_block(heading)
                        if data_block:
                            if data_block.name in ['ul', 'ol']:
                                inner_table = data_block.find('table')
                                if inner_table:
                                    headers, table_data = extract_table_rows(inner_table)
                                    if table_data:
                                        all_records[common_section] = table_data
                                        print(f"✓ '{common_section}': {len(table_data)}개 행 추출")
                                else:
                                    ul_data = extract_ul_list_rows(data_block)
                                    if ul_data:
                                        all_records[common_section] = ul_data
                                        print(f"✓ '{common_section}': {len(ul_data)}개 행 추출")
                            elif data_block.name == 'table':
                                headers, table_data = extract_table_rows(data_block)
                                if table_data:
                                    all_records[common_section] = table_data
                                    print(f"✓ '{common_section}': {len(table_data)}개 행 추출")
                        break

        if not any(all_records.values()):
            print("섹션 기반 추출이 비어 있어 전체 테이블 백업 탐색을 시도합니다.")
            tables = soup.find_all('table')
            for idx, table in enumerate(tables):
                headers, table_data = extract_table_rows(table)
                if not table_data:
                    continue

                header_set = set(headers)
                if '경기일자' in header_set:
                    all_records['최근_5경기'] = table_data
                elif '대회명' in header_set and '경기수' in header_set:
                    all_records['대회별_기록'] = table_data
                elif '연도' in header_set and '소속' in header_set:
                    all_records['연도별_기록'] = table_data
                elif '수상명' in header_set:
                    all_records['수상내역'] = table_data
                elif '지역' in header_set:
                    all_records['출신학교'] = table_data
                elif '평균자책점' in header_set or '이닝' in header_set:
                    all_records['2025_시즌'] = table_data
                else:
                    all_records.setdefault(f'테이블_{idx}', table_data)
        
        player_data['records'] = all_records
        
        return player_data
        
    except requests.RequestException as e:
        print(f"오류 발생: {e}")
        return None


def save_to_json(data, filename='player_data.json'):
    """JSON 파일로 저장"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"데이터를 {filename}에 저장했습니다.")


def save_to_csv(data, filename='player_data.csv'):
    """CSV 파일로 저장"""
    records = data.get('records', {})
    
    # 모든 레코드 타입을 개별 CSV로 저장
    saved_files = []
    for record_type, record_list in records.items():
        if record_list and isinstance(record_list, list):
            csv_filename = filename.replace('.csv', f'_{record_type}.csv')
            df = pd.DataFrame(record_list)
            # 명시적으로 콤마 구분자 지정
            df.to_csv(csv_filename, index=False, encoding='utf-8-sig', sep=',')
            saved_files.append(csv_filename)
            print(f"{record_type} 기록을 {csv_filename}에 저장했습니다. ({len(record_list)}개 행)")
    
    if not saved_files:
        print("저장할 시즌 기록이 없습니다.")
    
    return saved_files


def main():
    # 예제 1: 선수 201508002605의 타자 데이터 가져오기
    person_no = '201508002605'
    gubun = 'P#hitter'  # 'P': 투수, 'P#hitter': 타자
    
    # 예제 2: 선수 202301001889의 투수 데이터 가져오기
    # person_no = '202301001889'
    # gubun = 'P'  # 이 선수는 투수이므로 투수 기록 가져오기
    
    print(f"선수 정보를 가져오는 중... (person_no: {person_no}, gubun: {gubun})")
    # requests만 사용 (Selenium 불필요 - 모든 데이터가 HTML에 포함됨)
    player_data = get_player_data(person_no, gubun, debug=True, use_selenium=False)
    
    if player_data:
        print("\n=== 선수 기본 정보 ===")
        for key, value in player_data.items():
            if key not in ['records', 'person_no', 'url']:
                print(f"{key}: {value}")
        
        # 각 레코드 타입별 데이터 개수 출력
        if 'records' in player_data:
            print("\n=== 추출된 레코드 ===")
            for record_type, record_list in player_data['records'].items():
                if record_list:
                    print(f"{record_type}: {len(record_list)}개 행")
                    # 첫 번째 행 샘플 출력
                    if isinstance(record_list, list) and record_list:
                        print(f"  샘플: {list(record_list[0].keys())[:5]}")
        
        # 데이터 저장
        save_to_json(player_data)
        save_to_csv(player_data)
        
        print("\n완료!")
    else:
        print("데이터를 가져오지 못했습니다.")


if __name__ == '__main__':
    main()
