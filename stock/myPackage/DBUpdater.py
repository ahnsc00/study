import pandas as pd
import pymysql
import calendar, time, json
from datetime import datetime
from selenium import webdriver
from bs4 import BeautifulSoup
import re
from threading import Timer
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


class DBUpdater:
    def __init__(self):
        '''생성자 : MariaDB 연결 및 종목코드 딕셔너리 생성'''
        self.conn = pymysql.connect(host='localhost', user='root', password='dkstjdcks1206', db='INVESTAR', charset='utf8')
        with self.conn.cursor() as curs:
            sql = """
            CREATE TABLE IF NOT EXISTS company_info (
            code VARCHAR(20),
            company VARCHAR(40),
            last_update DATE,
            PRIMARY KEY (code))
            """
            curs.execute(sql)
            
            sql = """
            CREATE TABLE IF NOT EXISTS daily_price (
            code VARCHAR(20),
            date DATE,
            open BIGINT(20),
            high BIGINT(20),
            low BIGINT(20),
            close BIGINT(20),
            diff BIGINT(20),
            volume BIGINT(20),
            PRIMARY KEY (code, date))
            """
            curs.execute(sql)
            
        self.conn.commit()
        
        self.codes = dict()
        self.update_comp_info()
        
    def __del__(self):
        '''소멸자: MariaDB 연결 해제'''
        self.conn.close()
        
    def read_krx_code(self):
        '''krx로부터 상장법인목록 파일을 읽어와서 데이터프레임으로 반환'''
        krx = pd.read_html('C:/Users/201910810/workspace/Study/stock/data/상장법인목록.xls', header=0)[0]
        krx = krx[['종목코드', '회사명']]
        krx = krx.rename(columns={'종목코드':'code', '회사명':'company'})
        krx.code = krx.code.map('{:06d}'.format)
        return krx
    
    def update_comp_info(self):
        '''종목코드를 company_info 테이블에 업데이트한 후 딕셔너리에 저장'''
        sql = "SELECT * FROM company_info" 
        df = pd.read_sql(sql, self.conn) # company_info(종목코드 저장) 테이블 읽어옴
        for idx in range(len(df)): #df 열 수만큼 읽은 다음
            self.codes[df['code'].values[idx]]=df['company'].values[idx] # code(종목코드)와 company dict 형태로 매칭
        with self.conn.cursor() as curs: 
            sql = "SELECT max(last_update) FROM company_info" # company_info에서 last_update max(제일 최신 업데이트 날짜)
            curs.execute(sql)
            rs = curs.fetchone() # 가져옴
            today = datetime.today().strftime('%Y-%m-%d')
            
            if rs[0] == None or rs[0].strftime('%Y-%m-%d') < today: # 날짜가 존재하지 않거나 오늘보다 오래됨경우
                krx = self.read_krx_code() # df 불러와 서
                for idx in range(len(krx)):
                    code = krx.code.values[idx] 
                    company = krx.company.values[idx]
                    sql = f"REPLACE INTO company_info (code, company, last_update)  VALUES ('{code}', '{company}', '{today}')"
                    curs.execute(sql) # code, company, today 날짜 업데이트
                    self.codes[code] = company # dict에 code : company 매칭. 어따씀?
                    tmnow = datetime.now().strftime('%Y-%m-%d %H:%M')
                    print(f"[{tmnow}] {idx:04d} REPLACE INTO company_info VALUES({code}, {company}, {today})")
                
            self.conn.commit()
            print('')
            
    def read_naver(self, code, company, pages_to_fetch, driver):
        '''네이버 금융에서 주식 시세를 읽어서 데이터프레임으로 반환'''
        try:
            # 페이지 수 알아오기
            url = f'https://finance.naver.com/item/sise_day.nhn?code={code}' # 종목 하나의 전체 시세
            driver.get(url)
            req = driver.page_source
            if req is None:
                return None
            soup = BeautifulSoup(req, "html.parser")
            pgrr = soup.find('td', class_ = 'pgRR') # page Right Right
            if pgrr is None:
                return None
            lastpage = str(pgrr.a['href']).split('=')[-1]
            
            
            # 주식 시세를 읽어서 데이터프레임으로 반환
            df = pd.DataFrame()
            pages = min(int(lastpage), pages_to_fetch) # pages_to_fetch??
            for page in range(1, pages+1):
                page_url = '{}&page={}'.format(url, page)
                driver.get(page_url)
                df = df.append(pd.read_html(driver.page_source, header=0)[0], ignore_index = True)
                tmnow = datetime.now().strftime('%Y-%m-%d %H:%M')
                print('[{}] {} ({}) : {:04d}/{:04d} pages are downloading...'.format(tmnow, company, code, page, pages), end="\r")
            df = df.rename(columns = {'날짜':'date', '전일비':'diff', '시가':'open', '고가':'high', '저가':'low', '종가':'close', '거래량':'volume'})
            df['date'] = df['date'].replace('.','-')          
            df = df.dropna().reset_index().drop('index', axis = 1)
            df[['close','diff','open','high','low','volume']] = df[['close','diff','open','high','low','volume']].astype(int)
        except Exception as e:
            print('Exception occured :', str(e))
            return None
        return df
            
        
    def replace_into_db(self, df, num, code, company):
        '''네이버 금융에서 읽어온 주식 시세를 DB에 replace '''
        with self.conn.cursor() as curs:
            for r in df.itertuples(): # tuples
                sql = f"REPLACE INTO daily_price VALUES('{code}', "\
                    f"'{r.date}', {r.open}, {r.high}, {r.low}, {r.close},"\
                    f"{r.diff}, {r.volume})"
                curs.execute(sql)
            self.conn.commit()
            print('[{}] #{:04d} {} ({}) : {} rows > REPLACE INTO daily_'\
                 'price [OK]'.format(datetime.now().strftime('%Y-%m-%d %H:%M'), num+1, company, code, len(df)))
            
        
    def update_daily_price(self, pages_to_fetch):
        '''krx 상장법인의 주식 시세를 네이버로부터 읽어서 db에 업데이트'''
        # 속도 향상 위해 객체 생성하고 전달하는 방식으로 변경
        chrome_options = webdriver.ChromeOptions()
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        for idx, code in enumerate(self.codes):
            df = self.read_naver(code, self.codes[code], pages_to_fetch, driver)
            if df is None:
                continue
            self.replace_into_db(df, idx, code, self.codes[code])
        
        
    def execute_daily(self):
        '''실행 즉시 및 매일 오후 다섯시에 daily_price 테이블 업데이트'''
        self.update_comp_info() # 종목코드
        try:
            with open('config.json', 'r') as in_file:
                config = json.load(in_file)
                pages_to_fetch = config['pages_to_fetch']
        except FileNotFoundError:
            with open('config.json', 'w') as out_file:
                pages_to_fetch = 100
                config = {'pages_to_fetch':1}
                json.dump(config, out_file)
        self.update_daily_price(pages_to_fetch) # 시세 db에 저장

        tmnow = datetime.now()
        lastday = calendar.monthrange(tmnow.year, tmnow.month)[1] # 이 달이 몇일까지 있는지
        #다음날 오후 5시 계산
        if tmnow.month == 12 and tmnow.day == lastday: # 연도 마지막
            tmnext = tmnow.replace(year=tmnow.year+1, month=11, day=1, hour = 17, minute=0, second=0)
        elif tmnow.day == lastday: # 달의 마지막
            tmnext = tmnow.replace(month=tmnow.month+1, day=1, hour=17, minute=1, second=0)
        else :
            tmnext = tmnow.replace(day=tmnow.day+1, hour=17, minute=0, second =0)
        tmdiff = tmnext - tmnow
        secs = tmdiff.seconds
        
        t = Timer(secs, self.execute_daily)#execute_daily() 메서드 실행하는 타이머 객체 생성
        print("Waiting for next update ({})...".format(tmnext.strftime('%Y-%m-%d %H:%M')))
        t.start()# secs 뒤에 실행
        
if __name__ == '__main__':
    dbu = DBUpdater()
    dbu.execute_daily()