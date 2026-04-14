"""
고급 야구선수 데이터 스크래퍼 (Selenium 사용)
동적 콘텐츠가 있는 경우 사용
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import pandas as pd
import json
import time


class BaseballPlayerScraper:
    def __init__(self, headless=True):
        """
        Selenium 웹드라이버 초기화
        
        Parameters:
        - headless: True면 브라우저 창을 띄우지 않음
        """
        chrome_options = Options()
        if headless:
            chrome_options.add_argument('--headless')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
        
        self.driver = webdriver.Chrome(options=chrome_options)
        self.wait = WebDriverWait(self.driver, 10)
    
    def get_player_info(self, person_no, gubun='P'):
        """
        선수 정보 가져오기
        
        Parameters:
        - person_no: 선수 번호
        - gubun: 'P' (투수) 또는 'B' (타자)
        
        Returns:
        - dict: 선수 정보
        """
        url = f'https://www.korea-baseball.com/info/player/player_view?person_no={person_no}&gubun={gubun}'
        
        try:
            self.driver.get(url)
            time.sleep(2)  # 페이지 로딩 대기
            
            player_data = {}
            
            # 선수 기본 정보 추출
            try:
                name_element = self.driver.find_element(By.CSS_SELECTOR, 'table td')
                player_data['name'] = name_element.text
            except:
                player_data['name'] = 'Unknown'
            
            # 테이블 데이터 추출
            tables = self.driver.find_elements(By.TAG_NAME, 'table')
            
            all_records = []
            for table in tables:
                try:
                    # 헤더 추출
                    headers = []
                    header_cells = table.find_elements(By.TAG_NAME, 'th')
                    headers = [cell.text.strip() for cell in header_cells if cell.text.strip()]
                    
                    if not headers:
                        continue
                    
                    # 데이터 행 추출
                    rows = table.find_elements(By.TAG_NAME, 'tr')
                    for row in rows[1:]:  # 헤더 제외
                        cells = row.find_elements(By.TAG_NAME, 'td')
                        if cells:
                            row_data = {}
                            for i, cell in enumerate(cells):
                                if i < len(headers):
                                    row_data[headers[i]] = cell.text.strip()
                            if row_data:
                                all_records.append(row_data)
                except Exception as e:
                    print(f"테이블 파싱 오류: {e}")
                    continue
            
            player_data['records'] = all_records
            return player_data
            
        except Exception as e:
            print(f"오류 발생: {e}")
            return None
    
    def get_multiple_players(self, player_list):
        """
        여러 선수의 정보를 한 번에 가져오기
        
        Parameters:
        - player_list: [(person_no, gubun), ...] 형태의 리스트
        
        Returns:
        - list: 선수 정보 리스트
        """
        all_players = []
        
        for person_no, gubun in player_list:
            print(f"선수 정보 가져오는 중: {person_no} ({gubun})")
            player_data = self.get_player_info(person_no, gubun)
            if player_data:
                player_data['person_no'] = person_no
                player_data['gubun'] = gubun
                all_players.append(player_data)
            time.sleep(1)  # 서버 부하 방지
        
        return all_players
    
    def save_to_json(self, data, filename='players_data.json'):
        """JSON 파일로 저장"""
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"데이터를 {filename}에 저장했습니다.")
    
    def save_to_excel(self, data, filename='players_data.xlsx'):
        """Excel 파일로 저장"""
        if data and 'records' in data[0]:
            all_records = []
            for player in data:
                for record in player.get('records', []):
                    record['person_no'] = player.get('person_no', '')
                    record['name'] = player.get('name', '')
                    all_records.append(record)
            
            if all_records:
                df = pd.DataFrame(all_records)
                df.to_excel(filename, index=False, engine='openpyxl')
                print(f"데이터를 {filename}에 저장했습니다.")
    
    def close(self):
        """브라우저 종료"""
        self.driver.quit()


def main():
    # 스크래퍼 초기화
    scraper = BaseballPlayerScraper(headless=True)
    
    try:
        # 단일 선수 정보 가져오기
        player_data = scraper.get_player_info('201508002605', 'P')
        
        if player_data:
            print("\n=== 선수 정보 ===")
            print(json.dumps(player_data, ensure_ascii=False, indent=2))
            
            # 저장
            scraper.save_to_json([player_data], 'player_selenium.json')
        
        # 여러 선수 정보 가져오기 (예제)
        # player_list = [
        #     ('201508002605', 'P'),
        #     ('다른선수번호', 'B'),
        # ]
        # all_players = scraper.get_multiple_players(player_list)
        # scraper.save_to_json(all_players, 'all_players.json')
        # scraper.save_to_excel(all_players, 'all_players.xlsx')
        
    finally:
        scraper.close()


if __name__ == '__main__':
    main()
