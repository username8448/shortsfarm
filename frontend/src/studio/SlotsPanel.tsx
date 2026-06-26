import type {TemplateDefinition} from './template';
import {
  folderSectionLabel,
  slotLabel,
  slotPropertyLabel,
  slotValueLabel,
} from './labels';

export const SlotsPanel = ({
  definition,
  onChange,
}: {
  definition: TemplateDefinition;
  onChange: (definition: TemplateDefinition) => void;
}) => {
  const updateSlot = (slotKey: string, patch: Record<string, unknown>) => {
    onChange({
      ...definition,
      slots: {
        ...definition.slots,
        [slotKey]: {...definition.slots[slotKey], ...patch},
      },
    });
  };
  const toggleSection = (slotKey: string, section: string) => {
    const slot = definition.slots[slotKey];
    const current = slot.allowed_sections || [];
    updateSlot(slotKey, {
      allowed_sections: current.includes(section)
        ? current.filter((item) => item !== section)
        : [...current, section],
    });
  };
  return (
    <section className="ts-card">
      <div className="ts-card-head"><h2>Входные слоты</h2></div>
      <div className="slot-list">
        {Object.entries(definition.slots).map(([key, slot]) => (
          <article className="slot-card" key={key}>
            <div className="slot-title">
              <strong>{slotLabel(key)}</strong>
              <code>{key}</code>
              <span className="ts-badge">{slotValueLabel(slot.type)}</span>
            </div>
            <label className="ts-check">
              <input
                type="checkbox"
                checked={slot.required}
                onChange={(event) => updateSlot(key, {required: event.target.checked})}
              />
              Обязательный вход
            </label>
            {slot.allowed_sections ? (
              <div className="slot-options">
                <span>Разрешённые разделы</span>
                {['sources', 'cuts', 'prepared'].map((section) => (
                  <label className="ts-check" key={section}>
                    <input
                      type="checkbox"
                      checked={slot.allowed_sections?.includes(section)}
                      onChange={() => toggleSection(key, section)}
                    />
                    {folderSectionLabel(section)}
                  </label>
                ))}
              </div>
            ) : null}
            {slot.duration_policy ? (
              <p>{slotPropertyLabel('duration_policy')}: <b>{slotValueLabel(slot.duration_policy)}</b></p>
            ) : null}
            {slot.source ? (
              <p>{slotPropertyLabel('source')}: <b>{slotValueLabel(slot.source)}</b></p>
            ) : null}
            {slot.playback ? (
              <p>{slotPropertyLabel('playback')}: <b>{slotValueLabel(slot.playback)}</b></p>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
};
