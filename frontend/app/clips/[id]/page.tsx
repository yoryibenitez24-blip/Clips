import { ClipEditor } from "@/components/ClipEditor";

export default async function ClipPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ClipEditor clipId={id} />;
}
