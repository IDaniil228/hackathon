import CodeMirror from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";

export default function Editor({
  value,
  change,
  language,
  disabled,
}: {
  value: string;
  change: (value: string) => void;
  language: string;
  disabled: boolean;
}) {
  return (
    <CodeMirror
      value={value}
      height="300px"
      extensions={language.startsWith("python") ? [python()] : []}
      onChange={change}
      editable={!disabled}
      theme="none"
      aria-label="Решение задания"
    />
  );
}
