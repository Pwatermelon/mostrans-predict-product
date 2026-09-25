# Эмулятор устройств NDTP

По REST принимает конфиг, по TCP подключается к NDTP-серверу как бортовой терминал: выполняет handshake, затем периодически отправляет пакеты телематики.

Порт API: **18080**. Конфиг хранится в памяти и после рестарта сбрасывается.

---

## 1. Запуск из Docker-образа

```bash
docker load -i ndtp-telemetry-emulator.tar
docker run --rm -p 18080:18080 --name ndtp-emu ndtp-telemetry-emulator:1.0
```

Эмулятор должен достучаться до NDTP-сервера (`targetHost:targetPort`). Если сервер запущен на той же машине:

```bash
docker run --rm -p 18080:18080 --add-host=host.docker.internal:host-gateway \
  --name ndtp-emu ndtp-telemetry-emulator:1.0
```

В конфиге тогда указывать `"targetHost": "host.docker.internal"`.

Проверка, что API жив:

```bash
curl -s http://localhost:18080/api/cells | head
```



---

## 2. Как пользоваться

| Метод | URL | Назначение |
|---|---|---|
| `GET` | `/api/cells` | Справочник поддерживаемых ячеек и их полей |
| `GET` | `/api/config` | Текущий конфиг |
| `POST` | `/api/config` | Загрузить конфиг (полностью заменяет предыдущий и сразу стартует отправку) |

Остановить эмуляцию: `POST /api/config` с `"units": []`.

Минимальный пример:

```json
{
  "targetHost": "host.docker.internal",
  "targetPort": 9201,
  "units": [
    {
      "unitId": 1166336,
      "intervalMs": 5000,
      "autoGenerate": true,
      "cells": []
    }
  ]
}
```

```bash
curl -s -X POST http://localhost:18080/api/config \
  -H 'Content-Type: application/json' \
  -d @config.json
```

Невалидный конфиг → HTTP 400, тело `{ "timestamp", "status", "error", "message" }`.

---

## 3. Конфиг: поля

### 3.1 Корень

| Поле | Тип | Смысл |
|---|---|---|
| `targetHost` | string | Хост NDTP-сервера. Не должен быть пустым |
| `targetPort` | int, 1…65535 | TCP-порт NDTP-сервера |
| `units` | array | Список эмулируемых устройств |

### 3.2 `units[]`

| Поле | Тип | Смысл |
|---|---|---|
| `unitId` | long | ID устройства в NDTP, диапазон `0 … 2147483647`. Должен быть уникален в конфиге. Уходит в handshake и в NPL `peerAddress` |
| `intervalMs` | long > 0 | Период отправки в миллисекундах. Первый пакет уходит сразу после загрузки конфига |
| `cron` | string | Cron Spring Boot: `сек мин час день месяц день_недели`. Первый пакет — на ближайшем срабатывании, не сразу |
| `autoGenerate` | bool, default `false` | Случайная «живая» телематика вместо фиксированных значений |
| `cells` | array | Ячейки телематического пакета. Имена полей ячейки = ключи JSON |

Правила валидации:

- у юнита задано **ровно одно** из `intervalMs` или `cron`;
- `unitId` не повторяются;
- не больше одной ячейки `G6CellNav00` на юнит;
- неизвестный `type` ячейки → HTTP 400.

`autoGenerate: true` и пустой `cells` → эмулятор сам собирает набор: `G6CellNav00`, `G6CellUsi08`, `G6CellTermo16`, `G6CellIntSensor02`, `G6CellCan10`. Если ячейки заданы явно — рандомизируются только известные типы (`Nav00`, `Usi08`, `Termo16`, `IntSensor02`, `Can10`, `Lls15`), остальные уходят как в JSON.

Если `G6CellNav00` в конфиге нет, сервис всё равно вставляет синтетическую навигацию (нули, флаги N/E/valid).

Поле `timestamp` в `G6CellNav00` всегда ставит эмулятор (Unix-секунды). Задавать его в JSON не нужно.

---

## 4. Поведение на TCP

На каждый `unitId` открывается отдельное TCP-соединение к `targetHost:targetPort`.

1. Connect.
2. Handshake: пакет `NPH_SGC_CONN_REQUEST`.
3. Пауза 200 мс.
4. Пакет телематики `NPH_SND_REALTIME`.
5. Дальше по расписанию уходят только realtime-пакеты, сокет держится. При обрыве — reconnect и handshake заново.

Если предыдущая отправка ещё идёт, следующий тик пропускается. Ответы сервера эмулятор не разбирает (кроме факта приёма байт).

---

## 5. Спецификация пакетов NDTP

Все поля **little-endian**, структуры packed (без выравнивания).

CRC — CRC-16/Modbus (poly `0xA001`, init `0xFFFF`) по NPH-заголовку + NPH-телу; в заголовок NPL значение кладётся **со свапнутыми байтами**.

Каждый кадр:

```
[ NPL 15 байт ][ NPH 10 байт ][ тело ]
```

### 5.1 NPL (15 байт)

| Смещ. | Размер | Поле | Значение эмулятора |
|---|---|---|---|
| 0 | u16 | `signature` | `0x7E7E` |
| 2 | u16 | `dataSize` | длина NPH-заголовка + тела |
| 4 | 16 bit | flags | encryption = 0, crc = 0, delay = 0, остальное 0 |
| 6 | u16 | `crc` | CRC-16/Modbus(NPH + тело), байты переставлены |
| 8 | u8 | `type` | `0x02` = NPH |
| 9 | u32 | `peerAddress` | `unitId` |
| 13 | u16 | `requestId` | `0` |

### 5.2 NPH (10 байт)

| Смещ. | Размер | Поле | Handshake | Realtime |
|---|---|---|---|---|
| 0 | u16 | `serviceId` | `0` GENERIC_CONTROLS | `1` NAVDATA |
| 2 | u16 | `type` | `100` CONN_REQUEST | `101` REALTIME |
| 4 | 16 bit | flags | bit0 `request` = 1 | то же |
| 6 | u32 | `requestId` | счётчик 1, 2, 3… (wrap на `2³²−1`) | то же |

### 5.3 Handshake — тело 18 байт

Пакет `NPH_SGC_CONN_REQUEST`:

| Поле | Тип | Значение |
|---|---|---|
| `protoVersionHigh` | u16 | `6` |
| `protoVersionLow` | u16 | `2` |
| flags | 16 bit | encryption = 0, crc = 0, simulate = 0 |
| `peerAddress` | u32 | `unitId` |
| `maxPacketSize` | u32 | `65535` |
| `reserved` | u32 | `0` |

### 5.4 Realtime — тело = последовательность ячеек

Каждая ячейка:

```
[ type: u8 ][ number: u8 ][ payload ]
```

- `type` — идентификатор типа ячейки (см. таблицу ниже).
- `number` — индекс среди ячеек того же `type` в пакете (`0`, `1`, …). Нужен, когда в одном пакете несколько одинаковых датчиков (например, два ДУТ `G6CellUsi08`).
- `G6CellNav00` всегда идёт первой. Остальные — в порядке из конфига.

---

## 6. Ячейки телематики

Полный актуальный список полей: `GET /api/cells`. Ниже — бинарный layout и смысл полей.

### 6.1 `G6CellNav00` — type **0**, 26 байт (навигация)

Всегда присутствует в realtime-пакете.

| Поле JSON | Тип | Смысл |
|---|---|---|
| `timestamp` | u32 | Unix time, секунды (ставит эмулятор) |
| `longitude` | u32 | \|lon\| × 10 000 000 |
| `latitude` | u32 | \|lat\| × 10 000 000 |
| `extraDopBit0` | 1 bit | запрос голосовой связи |
| `extraDopBit1` | 1 bit | тревога |
| `extraDopBit2` | 1 bit | SOS |
| `extraDopBit3` | 1 bit | первое включение |
| `extraDopBit4` | 1 bit | питание от встроенного АКБ |
| `extraDopBit5` | 1 bit | широта: `1` = N, `0` = S |
| `extraDopBit6` | 1 bit | долгота: `1` = E, `0` = W |
| `extraDopBit7` | 1 bit | валидность координат: `1` = достоверны |
| `batVoltage` | u8 | напряжение батареи, 1 единица = 20 мВ |
| `speedAvg` | u16 | средняя скорость, км/ч |
| `speedMax` | u16 | максимальная скорость, км/ч |
| `course` | u16 | курс, 0…360° |
| `track` | u16 | пройденный путь, м (mod 65535) |
| `altitude` | u16 | высота над уровнем моря, м |
| `nsat` | u8 | число спутников |
| `pdop` | u8 | PDOP |

Биты `extraDopBit0…7` упакованы в один байт: `extraDopBit0` — LSB.

Пример: `longitude = 376173210`, `latitude = 557551234`, `extraDopBit5 = true`, `extraDopBit6 = true` → точка ≈ `55.7551234° N, 37.6173210° E`.

### 6.2 `G6CellIntSensor02` — type **2**, 26 байт (внутренние датчики)

| Поле JSON | Тип | Смысл |
|---|---|---|
| `an_in0`…`an_in3` | u16 | аналоговые входы |
| `di_in` | u8 | цифровые входы |
| `di_out` | u8 | цифровые выходы |
| `di0_counter`…`di3_counter` | u16 | счётчики дискретных входов |
| `odometer` | u32 | одометр |
| `csq` | u8 | уровень GSM-сигнала |
| `gprs_state` | u8 | состояние GPRS |
| `accel_energy` | u8 | энергия акселерометра |
| `ext_volt` | i8 | внешнее напряжение |

### 6.3 `G6CellUsi08` — type **8**, 6 байт (ДУТ УЗИ-M)

В одном пакете может быть несколько экземпляров (`number` = 0, 1, …).

| Поле JSON | Тип | Смысл |
|---|---|---|
| `det_status` | u8 | состояние датчика топлива |
| `level_mm` | u16 | уровень топлива, мм |
| `level_l` | u16 | уровень топлива, литры |
| `temperature` | u8 | температура бака |

### 6.4 `G6CellCan10` — type **10**, 37 байт (CAN)

| Поле JSON | Тип | Смысл |
|---|---|---|
| `secFlagStatus` | u32 | флаги состояния автомобиля / безопасности. `0xFFFFFFFF` = CAN-модуль не найден |
| `allTimeEngine` | u32 | полное время работы двигателя, часы × 100 (1 ч 15 мин = 125) |
| `allTrack` | u32 | полный пробег, км × 100 |
| `allFuelConsum` | u32 | полный расход топлива, л |
| `fuelLevel` | u16 | bit15: `1` = проценты, `0` = литры; bit0…14 — уровень |
| `speedTurnEngine` | u16 | обороты двигателя, rpm |
| `tEngine` | i16 | температура двигателя, °C |
| `speed` | u8 | скорость ТС, км/ч |
| `pressureAxis` | u16[5] | давление на оси 1…5, кг × 10 |
| `flagAlarm` | u32 | флаги аварий |

### 6.5 `G6CellTermo16` — type **16**, 8 байт (температура)

| Поле JSON | Тип | Смысл |
|---|---|---|
| `status` | u32 | `≠ 0` — нет связи с датчиком |
| `temp` | i32 | температура, °C |

### 6.6 `G6CellLls15` — type **15**, 50 байт (LLS)

| Поле JSON | Тип | Смысл |
|---|---|---|
| `status` | u16 | статус датчика |
| `main_float_level` | u32 | уровень основного поплавка |
| `temperature_average` | u32 | средняя температура продукта |
| `percent_of_volume` | u32 | процентное заполнение |
| `total_Volume` | u32 | общий объём |
| `weight` | u32 | масса |
| `density` | u32 | плотность |
| `net_Standard_Volume` | u32 | объём основного продукта |
| `level_of_water` | u32 | уровень подтоварной воды |
| `pressure` | u32 | давление |
| `vapor_temperature_average` | u32 | средняя температура паровой фазы |
| `vapor_Weight` | u32 | масса паровой фазы |
| `liquid_phase_Weight` | u32 | масса жидкой фазы |

### 6.7 Остальные типы (можно слать вручную через `cells`)

Рандомизация `autoGenerate` на них не действует — значения берутся из JSON как есть. Незаданные поля = нули протокола.

| type | Класс | Назначение | Основные поля JSON |
|---|---|---|---|
| 3 | `G6CellCrown03` | пассажиропоток «Корона» | `odometer`, `zone`, `corona_door_in1…4`, `corona_door_out1…4` |
| 4 | `G6CellIrma04` | IRMA, двери | `odometer`, `zone`, `irma_door_in1…4`, `irma_door_out1…4`, `irma_present_door1…4`, `irma_closed_door1…4` |
| 5 | `G6CellKdm05` | КДМ | `pgmEnable`, `pgmWidth`, `pgmDensity`, `ploughState`, `brushState` |
| 6 | `G6CellIdn06` | импульсный датчик | `num_impulse_min`, `num_impulse_max`, `time`, `num_overflow`, `num_impulse`, `pres_impulse` |
| 7 | `G6CellIdn07` | цифровой датчик | `value` (`0` норма, `1` тревога, `2` обрыв, `3` КЗ на массу, `4` КЗ на питание) |
| 9 | `G6CellReg09` | регистратор | `id`, `name` (массив 32 байт) |
| 12 | `G6CellRfid12` | RFID | `key` (массив 5 байт) |
| 13 | `G6CellPlo13` | плотность / уровень | `density`, `temperature`, `level` (float32), `levelUnit` |
| 14 | `G6CellBms14` | BMS | `max_temperature`, `min_cell_voltage`, `max_cell_voltage`, `voltage`, `code_error0…3`, `current` |
| 17 | `G6CellAlcohol1st17` | алколок 1 | `status`, `alcoEvent` |
| 18 | `G6CellCAN18` | расширенный CAN I/O | `an_in0…7`, `di_in`, `di_out`, `di0_counter…di7_counter` |
| 19 | `G6CellGSMstations19` | GSM-соты | `mcc`, `mnc`, `lac`, `cid`, `rssi`, `time_adv`, соты 1…6 |
| 20 | `G6CellM333CAN20` | флаги M333 | `flag_high`, `flag_low` |
| 21 | `G6CellAlcohol2nd21` | алколок 2 | статус, счётчики, серийник, интервалы, продукт, организация |
| 22 | `G6CellServerStatistics22` | статистика сервера | `id_max`, `id_min`, `tm_oldest`, `tm_oldest_unack`, `cnt_unack`, `cnt_unack_losted` |
| 23 | `G6CellTrackerStatistics23` | статистика трекера | `cnt_ack`, `cnt_ack_realtime`, `cnt_noack`, `cnt_connect` |
| 100 | `G6CellZipSensorData100` | сырой датчик | `id_sensor`, `flag`, `len_data`, `data` (40 байт) |

---

## 7. Автогенерация (`autoGenerate: true`)

Эмулятор ведёт состояние на каждый `unitId` и от тика к тику слегка меняет значения, имитируя движение:

- координаты стартуют около Москвы (`55.70 + unitId%1000/10000`, `37.50 + …`) и смещаются по курсу;
- скорость, курс, высота, спутники, PDOP, батарея дрейфуют в разумных диапазонах;
- `extraDopBit7` (валидность) всегда `true`;
- ДУТ: уровень мм/л и температура бака;
- CAN: моточасы и расход растут, обороты/температура двигателя плавают, `flagAlarm = 0`;
- внутренние датчики: счётчики дискретов растут, `gprs_state = 1`.

После каждого `POST /api/config` состояние генератора сбрасывается.
