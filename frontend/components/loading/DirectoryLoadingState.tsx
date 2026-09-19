import { SkeletonBlock } from './Skeleton';

/** Same row geometry as the explorer; unknown data never looks like no files. */
export function DirectoryLoadingState({ rows = 5 }: { rows?: number }) {
  return (
    <div role='status' aria-label='Loading files' aria-busy='true' className='w-full px-3 py-1'>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className='flex h-8 items-center gap-2'>
          <SkeletonBlock width={14} height={14} radius={3} />
          <SkeletonBlock width={`${48 + (index % 3) * 12}%`} height={11} radius={3} />
        </div>
      ))}
    </div>
  );
}
