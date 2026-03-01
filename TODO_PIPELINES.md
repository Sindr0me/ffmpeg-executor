# TODO: Pipeline API

> Пока не реализовано. Этот документ описывает дизайн фичи для будущей реализации.

## Зачем

Сейчас каждый шаг обработки — отдельный HTTP-запрос. Если нужно убрать тишину из 4 видео и склеить их, клиент должен сам:

1. Отправить 4 задачи `clear_silence`
2. Дождаться всех 4
3. Собрать 4 output URL
4. Отправить `concat_videos` с этими URL

Это 5+ запросов и логика оркестрации на стороне клиента.

Pipeline API позволит описать весь граф в одном запросе:

```json
POST /v1/pipeline
{
  "steps": [
    { "id": "s1", "preset": "clear_silence", "input_url": "https://.../v1.mp4", "output_filename": "c1.mp4" },
    { "id": "s2", "preset": "clear_silence", "input_url": "https://.../v2.mp4", "output_filename": "c2.mp4" },
    { "id": "s3", "preset": "clear_silence", "input_url": "https://.../v3.mp4", "output_filename": "c3.mp4" },
    { "id": "s4", "preset": "clear_silence", "input_url": "https://.../v4.mp4", "output_filename": "c4.mp4" },
    {
      "id": "final",
      "preset": "concat_videos",
      "input_url": "${s1.output_url}",
      "output_filename": "result.mp4",
      "preset_options": {
        "input_urls": ["${s2.output_url}", "${s3.output_url}", "${s4.output_url}"]
      }
    }
  ],
  "webhook_url": "https://your-site.com/webhook"
}
```

Шаги `s1–s4` выполняются **параллельно**. Когда все готовы — автоматически запускается `final` с подставленными URL.

---

## Граф зависимостей

Сервер разбирает `${step_id.output_url}` в полях каждого шага, строит DAG и выполняет шаги в топологическом порядке. Независимые шаги — параллельно.

```
s1 ──┐
s2 ──┤
     ├──► final
s3 ──┤
s4 ──┘
```

Поддерживаемые ссылки в полях шага:
- `${step_id.output_url}` — единственный выход (preset API)
- `${step_id.output_files.out_alias.url}` — конкретный выход (command API)

---

## API

### POST /v1/pipeline

**Тело запроса:**

| Поле | Тип | Обязательно | Описание |
|---|---|---|---|
| `steps` | array | ✅ | Массив шагов (см. ниже) |
| `webhook_url` | string | — | HTTPS URL для уведомления о завершении |

**Шаг (step):**

| Поле | Тип | Описание |
|---|---|---|
| `id` | string | Уникальный идентификатор шага в пределах pipeline |
| `type` | string | `"preset"` (по умолчанию) или `"command"` |
| `preset` | string | Имя пресета (если `type=preset`) |
| `ffmpeg_command` | string | FFmpeg-команда (если `type=command`) |
| `input_url` | string | URL источника или `${step_id.output_url}` |
| `input_files` | object | Для `type=command` — алиасы входных файлов |
| `output_filename` | string | Имя выходного файла |
| `output_files` | object | Для `type=command` — алиасы выходных файлов |
| `preset_options` | object | Опции пресета |

**Ответ `202`:**
```json
{ "pipeline_id": "a1b2c3d4-..." }
```

### GET /v1/pipeline/{pipeline_id}

```json
{
  "pipeline_id": "a1b2c3d4-...",
  "status": "RUNNING",
  "steps": [
    { "id": "s1", "status": "SUCCESS", "output_url": "https://..." },
    { "id": "s2", "status": "SUCCESS", "output_url": "https://..." },
    { "id": "s3", "status": "PROCESSING", "output_url": null },
    { "id": "s4", "status": "QUEUED",     "output_url": null },
    { "id": "final", "status": "PENDING",  "output_url": null }
  ],
  "output_url": null,
  "duration_seconds": null
}
```

Статусы pipeline: `QUEUED` → `RUNNING` → `SUCCESS` / `FAILED`

Статус шага: `PENDING` (ждёт зависимостей) → `QUEUED` → `DOWNLOADING` → `PROCESSING` → `UPLOADING` → `SUCCESS` / `FAILED`

---

## Что нужно реализовать

### 1. Модели БД

Новые таблицы:

```python
# models.py

class Pipeline(Base):
    __tablename__ = "pipelines"
    id              = Column(UUID, primary_key=True, default=uuid4)
    status          = Column(String, default="QUEUED")
    step_defs       = Column(JSON)          # исходные определения шагов
    webhook_url     = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=func.now())
    started_at      = Column(DateTime, nullable=True)
    finished_at     = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    error           = Column(Text, nullable=True)

class PipelineStep(Base):
    __tablename__ = "pipeline_steps"
    id            = Column(UUID, primary_key=True, default=uuid4)
    pipeline_id   = Column(UUID, ForeignKey("pipelines.id"))
    step_id       = Column(String)          # логический id из запроса ("s1", "final")
    status        = Column(String, default="PENDING")
    job_id        = Column(UUID, nullable=True)     # ссылка на jobs или commands
    output_url    = Column(Text, nullable=True)
    error         = Column(Text, nullable=True)
```

Миграция:
```sql
CREATE TABLE pipelines ( ... );
CREATE TABLE pipeline_steps ( ... );
CREATE INDEX ON pipeline_steps (pipeline_id);
```

### 2. Схемы (schemas.py)

```python
class PipelineStepDef(BaseModel):
    id: str
    type: Literal["preset", "command"] = "preset"
    preset: Optional[str] = None
    ffmpeg_command: Optional[str] = None
    input_url: Optional[str] = None
    input_files: Optional[dict] = None
    output_filename: Optional[str] = None
    output_files: Optional[dict] = None
    preset_options: dict = {}

class PipelineCreate(BaseModel):
    steps: list[PipelineStepDef]
    webhook_url: Optional[str] = None
```

### 3. Логика резолва зависимостей (utils/pipeline.py)

```python
import re

PLACEHOLDER_RE = re.compile(r"\$\{(\w+)\.([\w.]+)\}")

def resolve_placeholders(value: Any, resolved: dict[str, Any]) -> Any:
    """Рекурсивно заменяет ${step_id.field} на реальные значения."""
    if isinstance(value, str):
        def replacer(m):
            step_id, field = m.group(1), m.group(2)
            step_data = resolved.get(step_id, {})
            # поддержка вложенных путей: output_files.out_thumb.url
            for key in field.split("."):
                step_data = step_data.get(key, "") if isinstance(step_data, dict) else ""
            return str(step_data)
        return PLACEHOLDER_RE.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: resolve_placeholders(v, resolved) for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_placeholders(i, resolved) for i in value]
    return value

def build_dag(steps: list[PipelineStepDef]) -> dict[str, set[str]]:
    """Строит граф зависимостей: {step_id: {зависит_от...}}"""
    deps = {s.id: set() for s in steps}
    for step in steps:
        raw = json.dumps(step.dict())
        for match in PLACEHOLDER_RE.finditer(raw):
            dep_id = match.group(1)
            if dep_id != step.id:
                deps[step.id].add(dep_id)
    return deps

def get_ready_steps(deps: dict, completed: set) -> list[str]:
    """Возвращает шаги, все зависимости которых выполнены."""
    return [sid for sid, d in deps.items() if sid not in completed and d <= completed]
```

### 4. Celery task (tasks.py)

```python
@celery_app.task(bind=True)
def process_pipeline(self, pipeline_id: str):
    """
    Оркестратор пайплайна. Запускает готовые шаги параллельно,
    ждёт их завершения, резолвит плейсхолдеры, продолжает.
    """
    with Session() as db:
        pipeline = db.get(Pipeline, pipeline_id)
        pipeline.status = "RUNNING"
        pipeline.started_at = datetime.utcnow()
        db.commit()

    steps = {s.id: s for s in pipeline.step_defs_parsed}
    deps = build_dag(list(steps.values()))
    completed = set()        # step_id → done
    results = {}             # step_id → {output_url, ...}

    while len(completed) < len(steps):
        ready = get_ready_steps(deps, completed)
        if not ready:
            # нет готовых, но есть незавершённые → цикл ожидания
            time.sleep(2)
            # проверить статусы запущенных job_id → обновить completed
            _check_running_steps(db, pipeline_id, completed, results)
            continue

        # запустить все готовые шаги параллельно
        for step_id in ready:
            step_def = steps[step_id]
            resolved = resolve_placeholders(step_def.dict(), results)
            job_id = _submit_step(resolved)
            _save_step_job_id(db, pipeline_id, step_id, job_id)

        # пометить как "запущенные" (не completed)
        # следующая итерация будет ждать их через _check_running_steps
        ...
```

> Реализацию `_submit_step` и `_check_running_steps` — вынести в отдельные хелперы.

### 5. Эндпоинты (main.py)

```python
@app.post("/v1/pipeline", status_code=202)
async def create_pipeline(payload: PipelineCreate, db: AsyncSession = Depends(get_db)):
    pipeline = Pipeline(step_defs=payload.dict()["steps"], webhook_url=payload.webhook_url)
    db.add(pipeline)
    await db.commit()
    process_pipeline.delay(str(pipeline.id))
    return {"pipeline_id": str(pipeline.id)}

@app.get("/v1/pipeline/{pipeline_id}")
async def get_pipeline(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    ...
```

### 6. MCP-инструмент (mcp/server.py)

Добавить `ffmpeg_run_pipeline`:

```python
@mcp.tool(name="ffmpeg_run_pipeline")
async def ffmpeg_run_pipeline(params: RunPipelineInput) -> str:
    """
    Submit a multi-step pipeline where outputs of earlier steps
    can be referenced as inputs to later steps via ${step_id.output_url}.
    Steps without dependencies run in parallel automatically.
    """
    ...
```

---

## Замечания по реализации

**Concurrency воркера.** Сейчас `--concurrency=2`. Параллельные шаги будут ограничены этим значением — при 4 параллельных шагах 2 встанут в очередь. Перед запуском пайплайнов стоит поднять до 4+:
```yaml
command: ["celery", "-A", "app.tasks", "worker", "--concurrency=4", "--loglevel=info"]
```

**Обработка ошибок.** Если один шаг упал — вся pipeline переходит в `FAILED`. Незапущенные шаги помечаются `CANCELLED`. Запущенные дожидаются своего завершения перед остановкой.

**Циклы в DAG.** При создании pipeline проверять на циклические зависимости (топологическая сортировка). Возвращать `400` если есть цикл.

**Таймаут.** Добавить `pipeline_timeout` (например, 3600 сек) — если pipeline не завершился за это время, помечать как `FAILED`.

---

## Когда стоит реализовать

Имеет смысл добавлять когда:
- Появятся клиенты без AI-оркестрации (n8n flows, прямые HTTP-интеграции)
- Количество шагов в типовых сценариях вырастет выше 5
- Потребуется надёжная обработка ошибок с частичным retry на уровне шагов
