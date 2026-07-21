import * as React from "react";

function dragCarriesFiles(event: DragEvent): boolean {
  const types = event.dataTransfer?.types;
  if (!types) return false;
  return Array.from(types).includes("Files");
}

// Full-viewport drag-and-drop capture for the Brain home: any file dragged
// anywhere over the window lights up a drop overlay, and dropping routes the
// files into the existing upload/ingest flow. Non-file drags (text, links,
// in-app element drags) are ignored entirely.
export function useGlobalFileDrop(onFiles: (files: File[]) => void) {
  const [dragging, setDragging] = React.useState(false);
  const depthRef = React.useRef(0);
  const onFilesRef = React.useRef(onFiles);
  onFilesRef.current = onFiles;

  React.useEffect(() => {
    const onDragEnter = (event: DragEvent) => {
      if (!dragCarriesFiles(event)) return;
      event.preventDefault();
      depthRef.current += 1;
      setDragging(true);
    };
    const onDragOver = (event: DragEvent) => {
      if (!dragCarriesFiles(event)) return;
      // Without preventDefault the browser navigates to the dropped file.
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    };
    const onDragLeave = (event: DragEvent) => {
      if (!dragCarriesFiles(event)) return;
      depthRef.current = Math.max(0, depthRef.current - 1);
      if (depthRef.current === 0) setDragging(false);
    };
    const onDrop = (event: DragEvent) => {
      depthRef.current = 0;
      setDragging(false);
      if (!dragCarriesFiles(event)) return;
      event.preventDefault();
      const files = Array.from(event.dataTransfer?.files || []);
      if (files.length) onFilesRef.current(files);
    };
    const onDragEnd = () => {
      depthRef.current = 0;
      setDragging(false);
    };
    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    window.addEventListener("dragend", onDragEnd);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("dragend", onDragEnd);
    };
  }, []);

  return dragging;
}
