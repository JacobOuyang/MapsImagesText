// Finished sections played one after another as one video, picture and voice together (AVFoundation, no ffmpeg).
//
//   swiftc -O demo/video/join.swift -o out/demo/join
//   out/demo/join private/video/all_1.1x.mp4 private/video/0{1,2,3,4,5}_*_1.1x.mp4 [--audio all.wav]
//
// With --audio (demo/video/join.py makes it: every section's voice, padded with silence to its picture's length, as
// one file), that is the only sound track. Without it, each section's voice is placed at its start and the quiet
// stretches are left as gaps in the track, which some players skip, playing the voice-overs back to back: by the last
// section the voice ran about 4 s ahead of the picture.
import AVFoundation
import Foundation

let args = CommandLine.arguments
var parts = Array(args.dropFirst(2)), sound: String? = nil
if let i = parts.firstIndex(of: "--audio"), i + 1 < parts.count { sound = parts[i + 1]; parts.removeSubrange(i...(i + 1)) }
guard args.count >= 3, !parts.isEmpty else { print("usage: join out.mp4 section.mp4 ... [--audio sound.wav]"); exit(1) }
let outURL = URL(fileURLWithPath: args[1])
try? FileManager.default.removeItem(at: outURL)

let mix = AVMutableComposition()
let video = mix.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!
let audio = mix.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!
var at = CMTime.zero
for path in parts {
    let part = AVURLAsset(url: URL(fileURLWithPath: path))
    let v = part.tracks(withMediaType: .video)[0]
    try video.insertTimeRange(v.timeRange, of: v, at: at)
    if sound == nil, let a = part.tracks(withMediaType: .audio).first {  // the voice ends before the picture: the rest is quiet
        try audio.insertTimeRange(CMTimeRange(start: .zero, duration: min(a.timeRange.duration, v.timeRange.duration)), of: a, at: at)
    }
    at = CMTimeAdd(at, v.timeRange.duration)
}
if let sound = sound {  // one continuous track, cut to the picture's length
    let soundAsset = AVURLAsset(url: URL(fileURLWithPath: sound))  // kept in a variable: a track alone doesn't keep its asset alive
    let a = soundAsset.tracks(withMediaType: .audio)[0]
    let cut = CMTimeConvertScale(at, timescale: a.naturalTimeScale, method: .roundTowardZero)  // in the sound's own units (else -12780)
    try audio.insertTimeRange(CMTimeRange(start: a.timeRange.start, duration: min(a.timeRange.duration, cut)), of: a, at: .zero)
}
let export = AVAssetExportSession(asset: mix, presetName: AVAssetExportPresetHighestQuality)!
export.videoComposition = AVMutableVideoComposition(propertiesOf: mix)  // re-encode every frame: one continuous track,
export.outputURL = outURL  // without the skipped lead-in frames of each section that some players show (drifting late)
export.outputFileType = .mp4
let done = DispatchSemaphore(value: 0)
export.exportAsynchronously { done.signal() }
done.wait()
guard export.status == .completed else { print("failed: \(String(describing: export.error))"); exit(1) }
print("wrote \(args[1]): \(String(format: "%.1f", CMTimeGetSeconds(at))) s")
