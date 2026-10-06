---
name: backend-cache-control-quiz
description: >-
  Выбор заголовка Cache-Control по требованиям к ответу: неизменяемая статика,
  чувствительные данные, обязательная проверка свежести, личные данные с допустимым
  отставанием
area: backend
grade: middle
skills: [http-api, caching]
kind: quiz
difficulty: 5
expected_minutes: 4
params:
  domain:
    type: choice
    choices:
      - интернет-банк
      - новостной портал
      - маркетплейс
---

## Генерация

Опиши кейс в домене: {domain}. Команда настраивает кеширование одного вида ответов сервера, и между браузером и сервером стоят общие кеширующие прокси и CDN. Опиши, что это за ответы и какие требования к ним выдвинули продукт и безопасность.

## Вопрос

Какой заголовок ответа выполняет все требования кейса и при этом не делает лишних запросов к серверу?

## Варианты

### hashed-asset

Факты:
- content: это файлы скриптов, в имени каждого файла есть хеш его содержимого, и файл по одному адресу никогда не меняется
- reuse: браузеры и CDN должны хранить файл как можно дольше и не обращаться за ним к серверу повторно

Ответ:
+ Cache-Control: public, max-age=31536000, immutable
- Cache-Control: no-store
- Cache-Control: no-cache и ETag
- Cache-Control: private, max-age=60

### sensitive

Факты:
- content: это страница с номером карты и историей платежей пользователя
- security: служба безопасности требует, чтобы ответ не сохранялся ни на диске браузера, ни в одном промежуточном кеше

Ответ:
+ Cache-Control: no-store
- Cache-Control: public, max-age=31536000, immutable
- Cache-Control: no-cache и ETag
- Cache-Control: private, max-age=60

### revalidate

Факты:
- content: это общий для всех большой каталог в JSON, он меняется несколько раз в день в непредсказуемые моменты
- fresh: клиент перед каждым показом должен убедиться у сервера, что каталог не устарел
- traffic: если каталог не изменился, тело ответа не должно передаваться заново

Ответ:
+ Cache-Control: no-cache и ETag
- Cache-Control: public, max-age=31536000, immutable
- Cache-Control: no-store
- Cache-Control: private, max-age=60

### personal-minute

Факты:
- content: это личная сводка пользователя, она своя у каждого пользователя
- stale: сводка может отставать от реальных данных не больше чем на минуту
- traffic: в течение этой минуты браузер не должен обращаться к серверу за сводкой повторно
- shared: общие прокси и CDN не должны хранить сводку

Ответ:
+ Cache-Control: private, max-age=60
- Cache-Control: public, max-age=31536000, immutable
- Cache-Control: no-store
- Cache-Control: no-cache и ETag

## Нельзя раскрывать

- no-store
- no-cache
- immutable
- max-age
- etag
- private
- public
- if-none-match
- 304
