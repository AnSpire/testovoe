# Пункты d2 + e — NAT-gateway VM и перенос `app` во внутреннюю сеть

## Задача

- **d2**: отдельная VM Ubuntu 22.04 с двумя сетевыми интерфейсами (внешний +
  внутренний) и NAT-роутингом из внутренней сети во внешнюю.
- **e**: перенести VM `app` (из пункта b) во внутреннюю сеть и настроить выход
  во внешний мир через NAT на VM из пункта d.

## Архитектура

```
         внешняя сеть (vagrant-libvirt, NAT через хост)
                         │
                 ┌───────┴───────┐
                 │   gateway     │  eth0: DHCP (192.168.121.x)
                 │ (MASQUERADE,  │
                 │  ip_forward)  │  eth1: 192.168.56.1/24
                 └───────┬───────┘
                         │  внутренняя сеть "nat_lab_internal"
                         │  (чистый L2-сегмент, без своего DHCP/NAT)
                 ┌───────┴───────┐
                 │     app       │  eth1: 192.168.56.10/24
                 │ (FastAPI,     │        default route -> 192.168.56.1
                 │  systemd)     │  eth0: DHCP (только для Vagrant SSH,
                 └───────────────┘        без дефолтного маршрута)
```

`app` ходит во внешний мир (например, `GET /health` → ping `77.88.8.8`)
**только** через `eth1` → `gateway` → NAT (`MASQUERADE`) → внешняя сеть.
`eth0` у `app` оставлен исключительно для управления (Vagrant SSH) — у него
явно отключён дефолтный маршрут (`dhcp4-overrides: use-routes: false`), чтобы
весь "боевой" трафик шёл только через gateway.

## Как сделано

- **Vagrantfile**: добавлена VM `gateway` (роли — NAT-шлюз) и у обеих VM
  (`app` и `gateway`) второй интерфейс `private_network` в сети
  `nat_lab_internal` (`192.168.56.0/24`).
- **Ansible**:
  - `roles/box_dns_fix` — общая роль для фикса мёртвых DNS и DNSSEC
    (вынесена из `app_deploy`, теперь используется и `app`, и `gateway`,
    т.к. оба на одном боксе с одной и той же проблемой).
  - `roles/nat_gateway` (плейбук `gateway.yml`) — `net.ipv4.ip_forward=1`,
    `MASQUERADE` на внешнем интерфейсе, `FORWARD` правила (внутренняя →
    внешняя, и обратный `ESTABLISHED/RELATED`), сохранение правил через
    `iptables-persistent`. Внешний/внутренний интерфейс определяются
    динамически (`ansible_default_ipv4.interface` и "оставшийся"
    интерфейс), без хардкода имён.
  - `roles/app_network` (используется в `app.yml`) — перезаписывает netplan
    на `app`: `eth0` без дефолтного маршрута, `eth1` со статическим IP и
    дефолтным маршрутом через `192.168.56.1`.

## Проблема по пути: коллизия IP между gateway VM и самой libvirt-сетью

Самая серьёзная находка этого этапа. `vagrant-libvirt` для `private_network`
по умолчанию создаёт свою собственную libvirt-сеть с `forward mode='nat'` и
**собственным DHCP/IP на мосту хоста**, равным адресу, который мы указали для
VM (в нашем случае `192.168.56.1` — т.к. его же получил интерфейс `gateway`).
В итоге на одном L2-сегменте оказывалось два владельца адреса `.1`: сам мост
хоста (`virbr2`) и VM `gateway`.

**Симптом**: `app` пинговал `192.168.56.1` без проблем (отвечал либо хост,
либо VM — не важно, кто именно), но транзитный трафик (например, `ping
1.1.1.1`) полностью пропадал — пакет виден в `tcpdump` на исходящем
интерфейсе `app`, но **не доходит** до интерфейса `gateway` вообще (0 пакетов
в `tcpdump`, хотя `FORWARD`-правила и `ip_forward=1` на `gateway` были в
порядке). Причина — сам хост пытался НАТить/роутить этот трафик через свою
собственную (неправильно сконфигурированную) сеть вместо передачи его на
VM `gateway`.

Диагностика:
```bash
sudo virsh net-dumpxml nat_lab_internal
# forward mode='nat', <ip address='192.168.56.1' ...><dhcp>...</dhcp></ip>
```

**Фикс** — в Vagrantfile у обоих `private_network` блоков (`app` и `gateway`)
добавлены:
```ruby
libvirt__forward_mode: "none",
libvirt__dhcp_enabled: false,
libvirt__host_ip: "192.168.56.254"
```
Это делает libvirt-сеть чисто изолированным L2-сегментом: сама она не
роутит/НАТит и не поднимает DHCP, адрес моста хоста сдвинут на `.254`,
чтобы не конфликтовать с `gateway` (`.1`) и `app` (`.10`).

Поскольку сеть уже была создана с неправильными параметрами, Vagrant её
просто переиспользовал бы без изменений — потребовалось пересоздание:
```bash
vagrant halt app gateway
sudo virsh net-destroy nat_lab_internal
sudo virsh net-undefine nat_lab_internal
vagrant up gateway app
vagrant provision app     # т.к. первый прогон на свежей app упал на apt
                           # из-за отсутствия сети до фикса
```

## Проверка результата

```bash
$ vagrant ssh gateway -c "ip -brief addr"
eth0  UP  192.168.121.123/24   # внешняя (NAT через хост)
eth1  UP  192.168.56.1/24      # внутренняя

$ vagrant ssh app -c "ip route"
default via 192.168.56.1 dev eth1 proto static
192.168.56.0/24 dev eth1 ...
192.168.121.0/24 dev eth0 ...    # без default-маршрута на eth0

$ vagrant ssh app -c "ping -c3 1.1.1.1"
3 packets transmitted, 3 received, 0% packet loss   # ttl=60 (прошёл через gateway)

$ vagrant ssh app -c "systemctl is-active app.service; systemctl is-enabled app.service"
active
enabled

$ vagrant ssh app -c "curl -X POST -H 'Test: Hello' http://localhost:8000/"
Hello, World!

$ vagrant ssh app -c "curl -o /dev/null -w 'HTTP %{http_code}\n' -X POST http://localhost:8000/"
HTTP 403

$ vagrant ssh app -c "curl http://localhost:8000/health"
OK
```

`/health` пингует `77.88.8.8` изнутри `app`, у которой нет прямого маршрута
во внешний мир — значит, пинг реально ушёл через `gateway` по NAT. Это
финальное подтверждение, что весь Lab 1 (a → b → d2 → e) работает как единое
целое.

## Структура (добавилось к Lab 1)

```
infra/nat-lab/
├── Vagrantfile                      # VM app + VM gateway, обе с 2 NIC
├── ansible/
│   ├── app.yml                      # roles: box_dns_fix, app_network, app_deploy
│   ├── gateway.yml                  # roles: box_dns_fix, nat_gateway
│   └── roles/
│       ├── box_dns_fix/             # общий фикс DNS/DNSSEC бокса
│       ├── app_network/             # netplan app: eth1 как default route
│       ├── app_deploy/              # (без изменений в сути, см. b-отчёт)
│       └── nat_gateway/             # ip_forward + MASQUERADE + FORWARD
└── reports/
    ├── b-systemd-deploy.md
    └── d2-e-nat-gateway.md          # этот файл
```
