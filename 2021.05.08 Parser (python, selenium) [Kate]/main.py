"""
C cайта https://www.kinopoisk.ru/lists/top250/?is-redirected=1
1) спарсить название фильмов из топ-250
2) спарсить рейтинг
3) спарсить отзывы
"""
import os
import re
import sqlite3
from typing import List

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException

# pip install webdriver_manager
# from webdriver_manager.firefox import GeckoDriverManager
from webdriver_manager.chrome import ChromeDriverManager

import pandas as pd


def get_film_id(url: str) -> int:
    """ Извлекает из ссылки на страницу фильма id-фильма """
    m = re.search(r'/film/(\d+)/?', url)
    return int(m.group(1)) if m else None


def parse_films(driver) -> List[dict]:
    """ Собирает имя фильма, рейтинг из одной страницы top250 """
    films = []
    item_els = driver.find_elements_by_xpath(
        '//div[@class="desktop-rating-selection-film-item"]')
    for item_el in item_els:
        position_el = item_el.find_element_by_xpath(
            './/span[@class="film-item-rating-position__position"]')
        link_el = item_el.find_element_by_xpath(
            './/a[contains(@href,"/film")]')
        name_el = link_el.find_element_by_xpath(
            './/p[@class="selection-film-item-meta__name"]')
        rating_el = item_el.find_element_by_xpath(
            './/span[contains(@class,"rating__value")]')

        url = link_el.get_attribute('href').strip()
        film_id = get_film_id(url)
        if film_id is None:
            raise Exception(f'Can not get film id for "{url}"')

        position = int(position_el.text.strip())
        name = name_el.text.strip()
        rating = float(rating_el.text)

        films.append((position, film_id, name, rating, url))

        print(f'{position}. "{name}" ({rating}) {url}')
    return films


def open_new_tab(driver, url: str):
    driver.execute_script("window.open(arguments[0])", url)


def get_all_films(driver) -> List[dict]:
    """ Проходит по всем страницами top250 и собирает информацию о фильмах
        (без рецензий, рецензии отдельно собираются) """

    top_250_url = "https://www.kinopoisk.ru/lists/top250/"
    top_250_url_fmt = ("https://www.kinopoisk.ru/lists/top250/"
                       "?page={page_number}")
    films = []
    """ 
         Открываем сразу все 5 страниц top250 на
         разных вкладках браузера только потом парсим 
         так меньше вероятность получить 
         некорректные данные (при проседании рейтинга)
         во время парсинга
    """
    driver.get(top_250_url)
    for page_number in range(2, 6):
        url = top_250_url_fmt.format(page_number=page_number)
        open_new_tab(driver, url)

    # проходимся по вкладкам браузера
    # фиксим странный порядок вкладок
    handles = [driver.window_handles[0], ]+driver.window_handles[1:][::-1]
    for handle in handles:
        driver.switch_to.window(handle)  # активация вкладки
        films.extend(parse_films(driver))

    # закрываем все вкладки кроме одной
    for handle in driver.window_handles[1:]:
        driver.switch_to.window(handle)
        driver.close()
    driver.switch_to.window(driver.window_handles[0])

    return films


def parse_reviews(driver, film_id: int) -> List[dict]:
    """ Переходит на страницу с рецензиями и
         собирает информацию по id-фильма """
    reviews = []
    try:
        driver.get(f'https://www.kinopoisk.ru/film/{film_id}/press/')

        list_el = driver.find_element_by_xpath('//div[@class="text_list"]')
        item_els = list_el.find_elements_by_xpath(
            './/div[contains(@class,"item")]')
        for item_el in item_els:
            author_name_el = item_el.find_element_by_xpath(
                './/div[@class="name"]')
            description_el = item_el.find_element_by_xpath(
                './/div[@class="descr"]//a')
            author_name = author_name_el.text.strip()
            description = description_el.text.strip()
            reviews.append((None, author_name, description, film_id))

            print(f'[{author_name}] "{description}"')
    except NoSuchElementException:
        pass
    return reviews


def create_tables(cursor):
    """ Создает двае талбицы в БД SQLite """
    cursor.execute("""CREATE TABLE Films (
            id INTEGER PRIMARY KEY, 
            name TEXT, 
            rating REAL, 
            url TEXT,
            reviews INTEGER DEFAULT 0)""")

    cursor.execute("""CREATE TABLE TopFilms (
            position INTEGER PRIMARY KEY, 
            film_id INTEGER,
            FOREIGN KEY (film_id) REFERENCES Films(id))""")

    cursor.execute("""CREATE TABLE Reviews(
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            author TEXT,
            description TEXT,
            film_id INTEGER, 
            FOREIGN KEY (film_id) REFERENCES Films(id))""")


def main():
    database_name = 'kinopoisk.sqlite'
    db_exists = os.path.exists(database_name)

    driver = webdriver.Chrome(executable_path=ChromeDriverManager().install())
    try:
        """ 
           Если база уже создана, то пропускаем этап анализа страницы top250 
           и пытаемся собрать рецензии по фильмам которые в БД опираясь на поле-флаг reviews
           говорящий нам были ли спарсены ранее рецензии по этому фильму.
           Пропускание и добавление рецензии позволит продолжать работу с последнего места,
           если приложение упадет по каким-то причинам(пропадение интернета к примеру),
           не начиная парсинг рецензий полностью с нуля.
        """
        if not db_exists:
            films = get_all_films(driver)
            with sqlite3.connect(database_name) as connection:
                cursor = connection.cursor()
                create_tables(cursor)

                #  position, id, name, rating, url, reviews
                topFilms = [f[:2] for f in films]
                films = [f[1:] for f in films]

                cursor.executemany(
                    "INSERT OR IGNORE INTO Films VALUES (?,?,?,?,0)", films)

                cursor.executemany(
                    "INSERT OR REPLACE INTO TopFilms VALUES (?,?)", topFilms)

                connection.commit()

        with sqlite3.connect(database_name) as connection:
            # Получаем id фильмов для которых еще не выполнен парсинг рецензий
            cursor = connection.cursor()
            query = "SELECT id FROM Films WHERE reviews=0"
            cursor.execute(query)
            records = cursor.fetchall()
            film_ids = [record[0] for record in records]
            for i, film_id in enumerate(film_ids, start=1):
                print(f'{i}/{len(film_ids)}')
                reviews = parse_reviews(driver, film_id)
                if reviews:
                    # Добавляем рецензии по этому фильму в БД
                    # id , author, description, film_id
                    query = "INSERT INTO Reviews VALUES (?,?,?,?)"
                    cursor.executemany(query, reviews)

                # Помечаем в БД что уже добавили рецензии для этого фильма.
                query = "UPDATE Films SET reviews=1 WHERE id=?"
                cursor.execute(query, (film_id,))
                connection.commit()

            """ Показываем таблицу с фильмами в виде DataFrame 
                (сортируем по положению в top250)  """
            cursor = connection.cursor()
            query = """SELECT TopFilms.position, Films.name, Films.rating 
                       FROM TopFilms JOIN Films
                       WHERE TopFilms.film_id == Films.id
                       ORDER BY TopFilms.position"""
            cursor.execute(query)
            records = cursor.fetchall()
            columns = ('position', 'name', 'rating')
            films_df = pd.DataFrame.from_records(records, columns=columns)
            print(films_df)

            """ Показываем таблицу с рецензиям к фильмам в виде DataFrame """
            cursor = connection.cursor()
            query = """SELECT Films.name, Reviews.author, Reviews.description 
                       FROM Reviews JOIN Films
                       WHERE Films.id == Reviews.film_id;   
                    """
            cursor.execute(query)
            records = cursor.fetchall()
            columns = ('film', 'author', 'description')
            reviews_df = pd.DataFrame.from_records(records, columns=columns)
            print(reviews_df)

    finally:
        driver.quit()  # закрываем браузер


if __name__ == "__main__":
    main()
