import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Dictionary } from "../api/types";

export default function DictionariesPage() {
  const [dictionaries, setDictionaries] = useState<Dictionary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Dictionary | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    setLoading(true);
    try {
      setDictionaries(await api.listDictionaries());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить словари");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleImport = async (file: File) => {
    try {
      await api.importDictionary(file);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось импортировать файл");
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Удалить словарь? Прежние анализы с ним останутся.")) return;
    await api.deleteDictionary(id);
    await load();
  };

  if (loading) return <p className="muted">Загрузка…</p>;

  return (
    <div>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>Словари ключевых слов</h2>
          <div className="row">
            <button
              className="secondary"
              onClick={() => fileInputRef.current?.click()}
            >
              Импорт из .txt
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleImport(file);
                e.target.value = "";
              }}
            />
            <button onClick={() => setEditing({ id: 0, name: "", description: "", keywords: [], created_at: "", updated_at: "" })}>
              + Новый словарь
            </button>
          </div>
        </div>
        {error && <p className="error-text">{error}</p>}
        <p className="muted">
          Один словарь — один список слов или словосочетаний. Формат .txt для импорта —
          одна строка на слово, как в прежнем dict.txt.
        </p>
      </div>

      {dictionaries.length === 0 && (
        <div className="card muted">Словарей пока нет — импортируйте .txt или создайте новый.</div>
      )}

      {dictionaries.map((d) => (
        <div className="card" key={d.id}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <strong>{d.name}</strong>{" "}
              <span className="muted">— {d.keywords.length} слов</span>
              {d.description && <div className="muted">{d.description}</div>}
            </div>
            <div className="row">
              <button className="secondary" onClick={() => setEditing(d)}>
                Редактировать
              </button>
              <button className="danger" onClick={() => handleDelete(d.id)}>
                Удалить
              </button>
            </div>
          </div>
          <div className="keyword-list" style={{ marginTop: 10 }}>
            {d.keywords.map((k) => (
              <span className="keyword-chip" key={k}>{k}</span>
            ))}
          </div>
        </div>
      ))}

      {editing && (
        <DictionaryEditor
          dictionary={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await load();
          }}
        />
      )}
    </div>
  );
}

function DictionaryEditor({
  dictionary,
  onClose,
  onSaved,
}: {
  dictionary: Dictionary;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = dictionary.id === 0;
  const [name, setName] = useState(dictionary.name);
  const [description, setDescription] = useState(dictionary.description || "");
  const [keywordsText, setKeywordsText] = useState(dictionary.keywords.join("\n"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    const keywords = keywordsText
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    if (!name.trim()) {
      setError("Укажите название словаря");
      return;
    }
    if (keywords.length === 0) {
      setError("Добавьте хотя бы одно слово");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (isNew) {
        await api.createDictionary({ name, description, keywords });
      } else {
        await api.updateDictionary(dictionary.id, { name, description, keywords });
      }
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось сохранить словарь");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card" style={{ borderColor: "var(--accent)" }}>
      <h2>{isNew ? "Новый словарь" : `Редактирование: ${dictionary.name}`}</h2>
      <div style={{ marginBottom: 12 }}>
        <label>Название</label>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div style={{ marginBottom: 12 }}>
        <label>Описание (необязательно)</label>
        <input type="text" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div style={{ marginBottom: 12 }}>
        <label>Слова и словосочетания — по одному на строку</label>
        <textarea
          rows={10}
          value={keywordsText}
          onChange={(e) => setKeywordsText(e.target.value)}
          placeholder={"invasion russe\ntroupes russes\narmée russe"}
        />
      </div>
      {error && <p className="error-text">{error}</p>}
      <div className="row">
        <button onClick={save} disabled={saving}>
          {saving ? "Сохранение…" : "Сохранить"}
        </button>
        <button className="secondary" onClick={onClose} disabled={saving}>
          Отмена
        </button>
      </div>
    </div>
  );
}
