import type {TemplateDefinition} from './template';

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
      <div className="ts-card-head"><h2>Slots</h2></div>
      <div className="slot-list">
        {Object.entries(definition.slots).map(([key, slot]) => (
          <article className="slot-card" key={key}>
            <div className="slot-title">
              <strong>{key}</strong>
              <span className="ts-badge">{slot.type}</span>
            </div>
            <label className="ts-check">
              <input
                type="checkbox"
                checked={slot.required}
                onChange={(event) => updateSlot(key, {required: event.target.checked})}
              />
              Required input
            </label>
            {slot.allowed_sections ? (
              <div className="slot-options">
                <span>Allowed sections</span>
                {['sources', 'cuts', 'prepared'].map((section) => (
                  <label className="ts-check" key={section}>
                    <input
                      type="checkbox"
                      checked={slot.allowed_sections?.includes(section)}
                      onChange={() => toggleSection(key, section)}
                    />
                    {section}
                  </label>
                ))}
              </div>
            ) : null}
            {slot.duration_policy ? <p>Duration: <b>{slot.duration_policy}</b></p> : null}
            {slot.source ? <p>Source: <b>{slot.source}</b></p> : null}
            {slot.playback ? <p>Playback: <b>{slot.playback}</b></p> : null}
          </article>
        ))}
      </div>
    </section>
  );
};
