# Пункты g + h — деплой приложения и внешний доступ через Ingress

## Задача

- **g** — задеплоить приложение (из пунктов a–c) в кластер.
- **h** — Ingress для внешнего доступа, явно без `kubectl port-forward`.

## Образ

Кластер тянет уже собранный и запушенный в Lab 2 образ
`anspire/testovoe-app:latest` напрямую с Docker Hub — никакой пересборки и
никакого собственного registry. У нод есть обычный интернет через
дефолтную NAT-сеть vagrant-libvirt, так что `containerd` пуллит образ так
же, как любой другой публичный образ.

## Манифесты (`infra/k8s-lab/manifests/`)

- `app-deployment.yaml` — `Deployment`, 2 реплики, `podAntiAffinity`
  (preferred) по `kubernetes.io/hostname`, чтобы реплики по возможности
  разъезжались по разным нодам. `readinessProbe`/`livenessProbe` —
  `tcpSocket` на порт 8000, **не** `/health` — тот реально пингует внешний
  `77.88.8.8`, привязывать к этому перезапуск контейнера было бы
  нестабильно.
- `app-service.yaml` — `ClusterIP`, порт 8000.
- `ingress.yaml` — `Ingress` (`ingressClassName: nginx`), host `app.test`,
  маршрутизация на сервис приложения.

Применяются Ansible-ролью `k8s_apps` (та же роль, что ставит MetalLB и
ingress-nginx) через `kubectl apply -f` на control-plane.

## Внешний доступ (пункт h): MetalLB + ingress-nginx

- **MetalLB** в L2-режиме (`IPAddressPool` + `L2Advertisement`,
  `metallb-native.yaml`). Пул берётся из той же подсети, что и ноды (см.
  `infra/k8s-lab/reports/f-k8s-cluster.md`, грабля №5, про выбор свободных
  адресов).
- **ingress-nginx** (`deploy/static/provider/cloud/deploy.yaml`) —
  `Service.type: LoadBalancer`, MetalLB подхватывает его и выдаёт реальный
  внешний IP из пула.
- Оба шага обёрнуты в retry (`until`/`retries`) — admission-вебхуки MetalLB
  и ingress-nginx могут отвечать `503`/`connection refused` ещё несколько
  секунд после того, как их `Deployment` уже показывает `Available`
  (контроллер поднялся, но сам HTTP-сервер вебхука — чуть позже).

## Проверка результата

```
$ kubectl get pods -o wide -l app=testovoe-app
NAME                           READY   STATUS    NODE
testovoe-app-f5f8c7c9f-8lvfn   1/1     Running   k8s-worker2
testovoe-app-f5f8c7c9f-dxb2c   1/1     Running   k8s-worker1

$ kubectl -n ingress-nginx get svc ingress-nginx-controller
NAME                       TYPE           EXTERNAL-IP       PORT(S)
ingress-nginx-controller   LoadBalancer   192.168.121.243   80:.../443:...

$ kubectl get ingress
NAME           CLASS   HOSTS      ADDRESS
testovoe-app   nginx   app.test   192.168.121.243
```

Проверка **с хост-машины** (не изнутри кластера, не `port-forward`):
```bash
$ curl -X POST -H "Host: app.test" -H "Test: Hello" http://192.168.121.243/
Hello, World!

$ curl -o /dev/null -w 'HTTP %{http_code}\n' -X POST -H "Host: app.test" http://192.168.121.243/
HTTP 403

$ curl -H "Host: app.test" http://192.168.121.243/health
OK
```

Путь трафика: хост → libvirt-сеть (та же, что у нод) → MetalLB-анонс
внешнего IP (ARP, L2) → ingress-nginx → Service → Pod на одном из
воркеров. Ни разу не использован `kubectl port-forward`.

Реплики подтверждённо размазаны по двум разным воркерам (`podAntiAffinity`
сработал), что также демонстрирует отказоустойчивость — потеря одного
воркера не убивает приложение целиком.

Поднятый ранее повторный (идемпотентный) прогон `vagrant provision`
подтвердил: деплой, сервис и Ingress переживают повторный `kubectl apply`
без пересоздания (`changed`, но не ресеты состояния), приложение продолжает
отвечать на старый/новый внешний IP без вмешательства руками.
