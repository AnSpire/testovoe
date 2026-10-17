# Lab 4 — CI/CD-стенд на Jenkins (JCasC + Job DSL)

## Задача

Отдельное тестовое задание "на знание процессов CI/CD" (не часть основного
a–h): одна VM с Jenkins, который сам, без ручного запуска, тянет код из
удалённого Git-репозитория, удаляет из копии 1–2 файла и пушит результат
обратно, без единого клика по кнопкам в UI.

- **a** — Git на сервере.
- **b** — установка Jenkins.
- **c** — Job_1: Poll SCM, клонирует ветку в локальное хранилище.
- **d** — Job_2: удаляет 1–2 файла из проекта.
- **e** — Job_3: возвращает проект в Git.
- **f** — всё запускается автоматически, не руками.

## Отклонение от буквального текста задания

В тексте задания везде фигурирует ветка `master`. Реальный репозиторий
[`AnSpire/opd-sem6`](https://github.com/AnSpire/opd-sem6) такой ветки не
содержит — у него `main`. По согласованию с пользователем везде, где в
задании написано `master`, использована `main`: Poll SCM, клонирование в
Job_1. Push-ветка (`jenkins-result`) от этого не зависит.

## Расположение

```
infra/cicd-lab/
  Vagrantfile
  ansible.cfg
  ansible/
    group_vars/all.yml      # repo url/branch, shared_dir, files_to_delete
    site.yml
    roles/jenkins/
      tasks/main.yml
      templates/
        jenkins-casc.yaml.j2       # JCasC: security realm, credential, 3 job'а (Job DSL)
        job1-pipeline.groovy.j2
        job2-pipeline.groovy.j2
        job3-pipeline.groovy.j2
        jenkins-override.conf.j2   # systemd drop-in: секреты + отключение setup wizard
  reports/
```

## VM и секреты

- 1 VM, box `generic/debian12`, 2 vCPU / 4096 MB RAM, одна сетевая карта —
  дефолтная NAT-сеть vagrant-libvirt (как в Lab 3; Jenkins UI открывается
  прямо по DHCP-IP VM с хоста, `http://<ip>:8080`, без port-forward).
- Пароль администратора Jenkins и GitHub-токен **не хранятся в
  репозитории** — читаются из переменных окружения хоста `Vagrantfile`'ом
  (`ENV["JENKINS_ADMIN_PASSWORD"]`, `ENV["GIT_TOKEN"]`) и передаются в
  Ansible через `ansible.extra_vars`. Запуск:
  ```bash
  JENKINS_ADMIN_PASSWORD='...' GIT_TOKEN='ghp_...' vagrant up
  ```
  `GIT_TOKEN` — персональный токен GitHub (classic PAT, право `repo`,
  нужен и для клонирования, и для push).

## Как настроен Jenkins

- **Java 21** — пакет `temurin-21-jdk` из официального apt-репозитория
  Adoptium (Debian 12 "из коробки" несёт только OpenJDK 17).
- **Jenkins** — из официального `pkg.jenkins.io/debian-stable`. Оба
  репозитория подключены одним и тем же паттерном "apt-ключ +
  `signed-by=`", что уже использовался в Lab 3 для `pkgs.k8s.io`.
- Сразу после установки пакет Jenkins пытается стартовать сервис — он
  останавливается явно (`systemd: state: stopped`), пока не разложены
  плагины и конфиг, чтобы не словить случайный старт с мастером установки.
- **Плагины** (`git`, `workflow-aggregator`, `configuration-as-code`,
  `job-dsl`) ставятся через `jenkins-plugin-cli` прямо в
  `/var/lib/jenkins/plugins`, сервис в это время выключен.
- **JCasC** (`/etc/jenkins/casc_configs/jenkins.yaml`) задаёт:
  - локального пользователя `admin` с паролем из `${JENKINS_ADMIN_PASSWORD}`;
  - авторизацию `loggedInUsersCanDoAnything`;
  - глобальный credential `git-token` (`usernamePassword`, пароль —
    `${GIT_TOKEN}`) для клонирования и push;
  - три `pipelineJob(...)` под ключом `jobs:` (Job DSL) — то есть все три
    job'а определены кодом в JCasC, а не руками через UI.
- Сами переменные `${JENKINS_ADMIN_PASSWORD}`/`${GIT_TOKEN}` — это
  подстановка JCasC из переменных окружения процесса Jenkins; они
  передаются через systemd drop-in `/etc/systemd/system/jenkins.service.d/override.conf`
  (права `0600`, `root:root` — systemd читает его до запуска процесса, так
  что права `jenkins`-пользователя на файл не нужны), который также
  отключает мастер установки (`-Djenkins.install.runSetupWizard=false`).

## Три job'а

Тела пайплайнов — отдельные `.groovy`-файлы
(`/etc/jenkins/pipelines/job{1,2,3}.groovy`), а не Groovy-строки прямо
внутри JCasC YAML — чтобы не словить вложенные кавычки YAML → Groovy →
shell. Каждый `pipelineJob` в JCasC просто делает
`script(new File('/etc/jenkins/pipelines/jobN.groovy').text)`.

- **Job_1** — триггер `scm('H/2 * * * *')` (Poll SCM, раз в ~2 минуты).
  Пересоздаёт `/var/lib/jenkins/shared/project` (`rm -rf` + `mkdir -p`,
  не `git pull` — чтобы Job_2 всегда стартовал с чистого состояния ветки
  `main`), клонирует её через credential `git-token`. По успеху —
  `build job: 'Job_2', wait: false`.
- **Job_2** — список файлов на удаление задан **один раз**:
  `files_to_delete` в `group_vars/all.yml`, попадает в пайплайн через
  переменную окружения `FILES_TO_DELETE`. Сначала проверяет, что каждый
  файл существует; если хотя бы одного нет — явный `exit 1` с сообщением
  в лог (пункт f задания: ошибка, а не молчание). Только потом удаляет,
  коммитит от имени `jenkins` (`user.name`/`user.email` настроены в этом
  же шаге). По успеху — `build job: 'Job_3', wait: false`.
- **Job_3** — `git push ... HEAD:jenkins-result --force` через тот же
  credential. Никогда не трогает `main` — иначе Poll SCM в Job_1 увидел бы
  "новый" коммит в `main` и зациклил бы цепочку сам на себя.

## Проверка результата

```bash
cd infra/cicd-lab
JENKINS_ADMIN_PASSWORD='...' GIT_TOKEN='ghp_...' vagrant up
vagrant ssh -c "hostname -I"
```

С хост-машины:
```bash
curl -I http://<ip>:8080/login   # 200/403, без setup wizard
```

Вход `admin`/пароль из `JENKINS_ADMIN_PASSWORD` — в Jenkins уже видно три
job'а (`Job_1`, `Job_2`, `Job_3`), созданные без единого клика в UI.

Цепочка: ручной (или по расписанию) запуск `Job_1` → автоматически
поднимает `Job_2` → автоматически поднимает `Job_3`. На GitHub после этого:
ветка `jenkins-result` содержит коммит от `jenkins` без `DATA.md` и
`tests/test_smoke.py`, ветка `main` не затронута ни одним новым коммитом.

Негативная проверка (пункт f) подтверждена прогоном той же shell-логики
Job_2 прямо на реальном чекауте (с несуществующим файлом в списке) —
падает с `exit 1` и явными `ERROR:`/`FAILING:` сообщениями в лог, не
проходит молча.

### Реальный результат прогона (2026-10-18)

Поллинг Job_1 сработал сам, по расписанию, без единого ручного клика —
сразу поднял всю цепочку:
```
Job_1 -> SUCCESS (клонировал main)
Job_2 -> SUCCESS (удалил DATA.md, tests/test_smoke.py, закоммитил как jenkins)
Job_3 -> SUCCESS (запушил force в jenkins-result)
```
Проверка на GitHub API после прогона:
- В дереве `jenkins-result`: `DATA.md` и `tests/test_smoke.py` отсутствуют.
- Последний коммит `jenkins-result`: автор `jenkins <jenkins@cicd-lab.local>`,
  сообщение `Job_2: remove DATA.md tests/test_smoke.py`.
- Ветка `main`: последний коммит — тот же, что был до прогона (автор
  `anspire`), ни одного нового коммита — цепочка не затронула `main`.

## Грабли, реально пойманные при развёртывании

1. **Docker блокирует NAT свежей libvirt-сети** — та же грабля, что в Lab
   1/3. Симптом: `vagrant ssh` работает, но `ping 8.8.8.8` изнутри VM —
   100% потерь, apt виснет намертво на первой же задаче. Разовый фикс на
   хосте (не персистентно между перезагрузками):
   ```bash
   sudo iptables -I DOCKER-USER -i virbr1 -j ACCEPT
   sudo iptables -I DOCKER-USER -o virbr1 -j ACCEPT
   ```

2. **Бокс `generic/debian12` несёт ту же болезнь, что `generic/ubuntu2204`
   (мёртвые DNS 4.2.2.1/4.2.2.2/208.67.220.220), но зашита она иначе.**
   На Debian 12 нет netplan — DNS прописан прямо в
   `/etc/network/interfaces` (`dns-nameserver ...`, управляется
   `resolvconf`). Симптом отличался от прошлого раза: `ping 8.8.8.8`
   работал (ICMP до мёртвых резолверов доходит), но `apt-get update`/`curl`
   к реальным внешним хостам (`pkg.jenkins.io`, `packages.adoptium.net`)
   висли на резолвинге — то есть "сеть же работает" было ложным
   ощущением снова, просто через другой симптом. Фикс — `lineinfile` по
   трём конкретным строкам в `/etc/network/interfaces` (заменить на
   `8.8.8.8`/`1.1.1.1`, третью убрать) + `resolvconf -u` для немедленного
   применения без перезапуска сети.

3. **Ключ apt-репозитория Jenkins периодически ротируется и истекает.**
   Файл `jenkins.io-2023.key`, который официальная документация называла
   на момент написания первой версии плана, истёк (`gpg --show-keys`
   показал `[expired: 2026-03-26]`) — `apt-get update` падал с `NO_PUBKEY`.
   Актуальное имя на момент реального прогона —
   `jenkins.io-2026.key` (проверяется листингом
   `https://pkg.jenkins.io/debian-stable/`). Если грабля повторится в
   будущем — смотреть туда же.

4. **`jenkins-plugin-cli` не входит в apt-пакет `jenkins` для Debian/Ubuntu.**
   Этот инструмент идёт только в комплекте официального Docker-образа
   Jenkins. Для обычной apt-установки пришлось скачивать тот же
   jar-инструмент отдельно (репозиторий
   `jenkinsci/plugin-installation-manager-tool`, GitHub Releases) и
   запускать его через `java -jar jenkins-plugin-manager.jar --war
   /usr/share/java/jenkins.war --plugin-download-directory
   /var/lib/jenkins/plugins --plugins ...` от имени пользователя `jenkins`.

Первый `vagrant up` заметно дольше последующих — качаются `jenkins.war`-
зависимости и 4 плагина с транзитивными зависимостями с update-center.
Повторные `vagrant provision` идемпотентны (плагины, apt-ключи и jar
плагин-менеджера не перескачиваются, если уже на месте).
