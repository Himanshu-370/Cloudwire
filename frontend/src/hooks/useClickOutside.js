import { useEffect } from "react";

export function useClickOutside(ref, onClose, active = true) {
  useEffect(() => {
    if (!active) return undefined;
    function handleClick(e) {
      if (!ref.current?.contains(e.target)) onClose();
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [ref, onClose, active]);
}
