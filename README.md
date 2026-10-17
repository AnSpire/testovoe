# testovoe

Решение 8 и 9 задания (пункты a–h): Python-приложение, деплой на VM через
systemd, Docker + мониторинг, NAT-шлюз, Kubernetes-кластер с Ingress.
Плюс отдельное задание на CI/CD: Jenkins-стенд (Lab 4, `infra/cicd-lab/`).

## Почему всё локально

Ответа насчёт облачной инфраструктуры под задание я не получил, поэтому решил
делать всё локально, на своей машине. Все VM поднимаются из кода и
настраиваются автоматически, руками внутри них ничего не делается. Стенд
целиком пересоздаётся командами `vagrant destroy` / `vagrant up`, и тот же код
без больших изменений переносится на облачные VM.

## Стек

- **Хост:** Arch Linux.
- **Виртуализация:** KVM + **libvirt** (не VirtualBox: libvirt — нативный для
  Linux гипервизор, и в нём удобно описывать отдельные сети).
- **Vagrant** с плагином **vagrant-libvirt** описывает VM и сети кодом
  (`Vagrantfile`). Box для всех VM — `generic/ubuntu2204` (Ubuntu 22.04).
- **Ansible** настраивает VM. Запускается самим Vagrant при `vagrant up` /
  `vagrant provision`, всё разбито на роли.
- **Приложение:** Python 3.13 + FastAPI, зависимости через **uv**. На Ubuntu
  22.04 есть только Python 3.10, а uv сам скачивает нужный Python и не трогает
  системный.
- **Docker / Docker Compose**, **Prometheus**, **Alertmanager**. Образ лежит в
  Docker Hub: `anspire/testovoe-app:latest`.
- **Kubernetes:** **kubeadm** (1 control-plane + 2 worker), CNI **Calico**
  (VXLAN), **MetalLB** (L2) + **ingress-nginx**.
- **CI/CD (Lab 4):** **Jenkins** на Debian 12 (`generic/debian12`), настроен
  целиком кодом: **JCasC** + **Job DSL**.

## Как велась работа

9 пункт разбил на 2 части: nat-lab (подпункты a, b,d, e) и k8s-lab (подпункты a,c,d,f,g,h)
Cпроектировал стенд, выбрал стек, определил порядок работ
и принимал все решения. Рутинную часть, то есть написание Vagrantfile,
Ansible-ролей и манифестов по моим решениям, я отдал ИИ-агенту (Claude Code),
чтобы сосредоточиться на архитектуре и отладке.

- **Спланировал работу.** Разбил задание на три независимые лабы и вёл их по
  циклу: реализация → проверка → отчёт → коммит → следующая лаба.
- **Принял архитектурные решения:**
  - libvirt/KVM: нативная для Linux виртуализация с гибкой
    настройкой сетей;
  - kubeadm: «настоящий» кластер, а не упрощённый дистрибутив;
  - MetalLB + ingress-nginx вместо NodePort: полноценный внешний доступ через
    LoadBalancer, как в облаке;
  - uv вместо pip/venv: нужный Python 3.13 без вмешательства в систему;
  - Docker Hub как единый источник образа для Docker- и Kubernetes-лаб;
  - топология сетей каждой лабы: внешняя и внутренняя сеть, NAT-шлюз, одна
    плоская сеть для кластера.
- **Контролировал реализацию.** Ставил агенту задачи, разбирал каждое его
  предложение и не принимал решения «на веру». Если предложенный вариант меня
  не устраивал, отправлял на доработку. Финальное слово всегда было за мной.
- **Проверял всё на живых VM.** Каждая лаба поднята с нуля, все эндпоинты
  проверены `curl`'ом, повторный `vagrant provision` проходит без ошибок
  (идемпотентность).
- **Отлаживал проблемы.** Диагностику на хосте (iptables/nftables, `virsh`,
  состояние мостов и сетей libvirt) проводил сам. Агент помогал быстрее
  перебирать гипотезы и предлагал варианты фиксов, а я вникал в причину
  каждой проблемы и выбирал, как её исправить.

### Проблемы, которые возникали

Половина проблем маскировала друг друга: следующая проявлялась только после
того, как была исправлена предыдущая.

1. **Docker на хосте ломал NAT libvirt-сетей.** Docker ставит политику
   `FORWARD DROP` и пропускает только свой трафик, поэтому у VM не было
   интернета, хотя `vagrant ssh` работал. Причина видна в
   `nft list table ip filter`. Исправил, разрешив трафик моста libvirt в
   цепочке `DOCKER-USER` (команды ниже, в разделе про хост).
2. **Сеть vagrant-libvirt «зависала» с нерабочим NAT.** Перезапуск сети не
   помогал, помогло полное пересоздание (`virsh net-destroy` + `net-undefine`).
3. **В box'е `generic/ubuntu2204` прописаны мёртвые DNS-серверы**
   (`4.2.2.1`, `4.2.2.2`, …) сразу в двух местах (netplan и
   `resolved.conf`). Хосты пингуются, а DNS на них не отвечает. Исправлено
   отдельной Ansible-ролью `box_dns_fix`.
4. **Там же включён `DNSSEC=yes`**, из-за чего резолвинг через NAT падал
   (`DNSSEC validation failed: missing-key`). Отключено той же ролью.
5. **Ansible `become_user` на пользователя без шелла** падал на `chmod +a`
   (синтаксис BSD-ACL). Исправлено через `allow_world_readable_tmpfiles` в
   `ansible.cfg`.
6. **Коллизия IP между gateway и самой libvirt-сетью.** `private_network` по
   умолчанию создаёт NAT-сеть со своим IP на мосту хоста, и этот IP совпал с
   адресом gateway. Прямой ping работал, а транзитный трафик пропадал без
   следа. Локализовал через `tcpdump` и `virsh net-dumpxml`, исправил явными
   параметрами сети в `Vagrantfile`.
7. **Calico в BGP-режиме ломал внешнюю сеть нод Kubernetes.** Это самая
   серьёзная находка. BIRD анонсировал соседям маршрут до общей подсети нод,
   и у них появлялся неправильный /32-маршрут к шлюзу через оверлей. SSH и
   интернет на нодах периодически пропадали. Вторая сетевая карта и
   `IP_AUTODETECTION_METHOD` не помогли, помог переход Calico на VXLAN.
8. **IPv6 на нодах был включён, но не работал**, поэтому образы с Docker Hub
   скачивались то с таймаутом, то нормально. IPv6 на нодах отключён.
9. **MetalLB не принимает одиночный IP без маски** (нужно `x.x.x.x/32`).
10. **DHCP libvirt раздаёт адреса не по порядку.** Конец подсети, выделенный
    под пул MetalLB, совпал с IP одной из нод. Теперь пул вычисляется по
    свободным адресам (ping-проверка + исключение IP нод) и не меняется при
    повторном прогоне.

Подробные разборы лежат в отчётах каждой лабы (ссылки ниже).

## Структура репозитория

| Пункты | Лаба | Где лежит | Отчёты |
|---|---|---|---|
| a | Приложение | `main.py`, `pyproject.toml`, `uv.lock` | — |
| b, d2, e | Lab 1 — systemd + NAT-шлюз | `infra/nat-lab/` | [b](infra/nat-lab/reports/b-systemd-deploy.md), [d2+e](infra/nat-lab/reports/d2-e-nat-gateway.md) |
| c, d1 | Lab 2 — Docker + мониторинг | `Dockerfile`, `docker-compose.yml`, `monitoring/` | [c+d1](reports/c-d1-docker.md) |
| f, g, h | Lab 3 — Kubernetes | `infra/k8s-lab/` | [f](infra/k8s-lab/reports/f-k8s-cluster.md), [g+h](infra/k8s-lab/reports/g-h-app-ingress.md) |
| CI/CD | Lab 4 — Jenkins | `infra/cicd-lab/` | [a–f](infra/cicd-lab/reports/a-f-jenkins-cicd.md) |

## Приложение (пункт a)

- `POST /` возвращает `Hello, World!`, если передан заголовок `Test: Hello`,
  иначе `403`.
- `GET /health` пингует `77.88.8.8`: при успехе `200 OK`, иначе `503`.
- `GET /metrics` — метрики для Prometheus (добавил для Lab 2, на поведение
  эндпоинтов выше не влияет).

Локальный запуск:

```bash
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8000

curl -X POST -H 'Test: Hello' localhost:8000/   # Hello, World!
curl -X POST localhost:8000/                    # 403
curl localhost:8000/health                      # OK
```

## Что нужно на хосте

Для Lab 1 и Lab 3:

- KVM + libvirt (`libvirtd` запущен, пользователь в группе `libvirt`);
- Vagrant + плагин `vagrant plugin install vagrant-libvirt`;
- Ansible (на хосте, Vagrant запускает его сам);
- по ресурсам: Lab 1 — 2 VM по 1 vCPU / 1 GB, Lab 3 — 3 VM по 2 vCPU,
  2–2.5 GB (~6.5 GB RAM всего).

Для Lab 2: Docker + Docker Compose.

**Если на хосте одновременно стоит Docker**, у VM не будет интернета
(проблема №1). После первого `vagrant up` (когда появился мост `virbr1`) и
после каждой перезагрузки хоста нужно выполнить:

```bash
sudo iptables -I DOCKER-USER -i virbr1 -j ACCEPT
sudo iptables -I DOCKER-USER -o virbr1 -j ACCEPT
```

Имя моста можно уточнить через `sudo virsh net-info vagrant-libvirt`
(поле `Bridge`).

## Lab 1 — systemd + NAT-шлюз (пункты b, d2, e)

Две VM:

- `gateway` — два интерфейса: внешний (NAT-сеть vagrant-libvirt) и
  внутренний `192.168.56.1`. Включены `ip_forward` и MASQUERADE.
- `app` — приложение как systemd-сервис `app.service` от системного
  пользователя `appuser`. Работает во внутренней сети `192.168.56.10`, наружу
  ходит только через NAT на `gateway`.

```bash
cd infra/nat-lab
vagrant up gateway   # сначала шлюз: без него у app нет интернета
vagrant up app
```

Проверка:

```bash
vagrant ssh app -c "systemctl is-active app.service"            # active
vagrant ssh app -c "ip route"                                   # default via 192.168.56.1
vagrant ssh app -c "curl -X POST -H 'Test: Hello' localhost:8000/"   # Hello, World!
vagrant ssh app -c "curl -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/"  # 403
vagrant ssh app -c "curl localhost:8000/health"                 # OK (ping идёт через gateway)
```

Если `vagrant up app` упал на шаге apt (например, gateway ещё не успел
подняться), достаточно повторить `vagrant provision app`.

## Lab 2 — Docker + мониторинг (пункты c, d1)

Lab 2 работает без VM, прямо на хосте, из корня репозитория.

```bash
docker compose up -d --build
```

Проверка:

```bash
curl -X POST -H 'Test: Hello' localhost:8000/   # Hello, World!
curl localhost:8000/health                      # OK
curl localhost:8000/metrics | head              # метрики приложения
# Prometheus:   http://localhost:9090/targets  — target app в состоянии UP
# Alertmanager: http://localhost:9093
```

Сборка и публикация образа (его использует Lab 3):

```bash
docker compose build
docker push anspire/testovoe-app:latest
```

## Lab 3 — Kubernetes (пункты f, g, h)

Три VM (`k8s-master`, `k8s-worker1`, `k8s-worker2`) с kubeadm-кластером и
Calico (VXLAN). Приложение — `Deployment` на 2 реплики (разнесены по разным
воркерам), `Service` и `Ingress` на хост `app.test`. Внешний IP выдаёт MetalLB
из свободных адресов подсети нод, его обслуживает ingress-nginx.
`kubectl port-forward` нигде не используется.

```bash
cd infra/k8s-lab
vagrant up   # ~10–15 минут: Ansible запускается один раз, после третьей VM, на весь кластер
```

Проверка:

```bash
vagrant ssh k8s-master -c "kubectl get nodes"     # 3 ноды Ready
vagrant ssh k8s-master -c "kubectl get pods -o wide -l app=testovoe-app"

# внешний IP ingress-nginx (выдан MetalLB):
IP=$(vagrant ssh k8s-master -c \
  "kubectl -n ingress-nginx get svc ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].ip}'" \
  2>/dev/null | tr -d '\r')

# с хоста, без port-forward:
curl -X POST -H "Host: app.test" -H "Test: Hello" http://$IP/            # Hello, World!
curl -o /dev/null -w '%{http_code}\n' -X POST -H "Host: app.test" http://$IP/  # 403
curl -H "Host: app.test" http://$IP/health                              # OK
```

## Lab 4 — Jenkins (задание на CI/CD)

Одна VM с Jenkins, который настраивается полностью кодом: без мастера
установки и без единого клика в UI. Три job'а описаны через Job DSL прямо в
конфиге JCasC. Цепочка запускается сама:

1. `Job_1` по Poll SCM (раз в 2 минуты) замечает изменения в ветке `main`
   репозитория [`AnSpire/opd-sem6`](https://github.com/AnSpire/opd-sem6) и
   клонирует её.
2. `Job_2` удаляет `DATA.md` и `tests/test_smoke.py` и коммитит изменения.
   Если какого-то файла нет, job падает с явной ошибкой, а не проходит молча.
3. `Job_3` пушит результат в отдельную ветку `jenkins-result`. В `main` он не
   пушит никогда, иначе Poll SCM зациклил бы цепочку.

В задании фигурирует ветка `master`, но в репозитории она называется `main`,
поэтому везде используется `main`. Список удаляемых файлов задаётся в одном
месте: `infra/cicd-lab/ansible/group_vars/all.yml`.

Пароль админа Jenkins и GitHub-токен в репозитории не хранятся. Они
передаются через переменные окружения при запуске:

```bash
cd infra/cicd-lab
JENKINS_ADMIN_PASSWORD='...' GIT_TOKEN='ghp_...' vagrant up   # GIT_TOKEN — GitHub PAT с правом repo
vagrant ssh -c "hostname -I"                                  # IP VM
# Jenkins UI: http://<ip>:8080, логин admin
```

Проверка: через пару минут после старта в Jenkins видно прошедшую цепочку
`Job_1 → Job_2 → Job_3`. На GitHub в ветке `jenkins-result` появляется коммит
от `jenkins` без двух удалённых файлов, а `main` не меняется.

На хосте нужно то же, что для Lab 1/3 (libvirt, Vagrant, Ansible, фикс
`DOCKER-USER`); VM занимает 2 vCPU / 4 GB.

## Уборка

```bash
(cd infra/nat-lab && vagrant destroy -f)
(cd infra/k8s-lab && vagrant destroy -f)
(cd infra/cicd-lab && JENKINS_ADMIN_PASSWORD=x GIT_TOKEN=x vagrant destroy -f)
docker compose down   # из корня репозитория
```
