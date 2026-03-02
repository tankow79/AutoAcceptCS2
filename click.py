import time
import pyautogui

def click_center_three_times(y_cordinate=0):
    # Дай собі 2 секунди, щоб відкрити потрібне вікно/екран
    time.sleep(3)

    # Розмір (ширина, висота) основного монітора
    width, height = pyautogui.size()

    # Центральна точка
    x = width // 2
    y = height // 2
    y = y - y_cordinate

    pyautogui.moveTo(x, y)

    # # 1-й клік
    pyautogui.click(x, y)
    time.sleep(0.5)
    #
    # # 2-й клік
    pyautogui.doubleClick(x, y)
    time.sleep(1)
    #
    # # 3-й клік
    pyautogui.click(x, y)
    time.sleep(1)


def click_center_three_times_test(y_cordinate=0):
    # Дай собі 2 секунди, щоб відкрити потрібне вікно/екран
    time.sleep(8)

    # Розмір (ширина, висота) основного монітора
    width, height = pyautogui.size()

    # Центральна точка
    x = width // 2
    y = height // 2
    y = y - y_cordinate

    pyautogui.moveTo(x, y)