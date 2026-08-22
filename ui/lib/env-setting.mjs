// JavaScript twin of hex_service_kit.netdefaults.read_env_setting.

export class ConfiguredEmptyError extends Error {}

/** Resolve unset, configured-empty and configured-value as three distinct states. */
export function readEnvSetting(env, name) {
  const present = env[name] !== undefined && env[name] !== null;
  const raw = present ? String(env[name]) : undefined;
  const value = raw === undefined ? "" : raw.trim();
  return {
    name,
    raw,
    value,
    isUnset: !present,
    isConfiguredEmpty: present && value === "",
    hasValue: present && value !== "",
  };
}

/** Use a non-empty default only for an absent variable; an emptied value refuses. */
export function settingOrDefault(env, name, defaultValue) {
  const setting = readEnvSetting(env, name);
  if (setting.isConfiguredEmpty) {
    throw new ConfiguredEmptyError(
      `${name} is configured but empty; unset it for ${JSON.stringify(defaultValue)} or give it a value`,
    );
  }
  return setting.hasValue ? setting.value : defaultValue;
}
