using System.Drawing;
using ZXing;
using ZXing.Common;

namespace AgentQR;

public sealed class QrScanner
{
    public const long MaxFileBytes = 25_000_000;
    public const long MaxPixels = 40_000_000;

    public IReadOnlyList<string> Decode(string path)
    {
        // Reject direct network/device paths; stage untrusted inputs in a local directory.
        if (!IsLocalPath(path)) throw new IOException("LOCAL_IMAGE_REQUIRED");
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        if (stream.Length == 0 || stream.Length > MaxFileBytes)
            throw new IOException("INVALID_IMAGE_SIZE");

        using var image = Image.FromStream(stream, useEmbeddedColorManagement: false, validateImageData: false);
        ValidateDimensions(image.Width, image.Height);
        using var bitmap = new Bitmap(image);
        var reader = new ZXing.Windows.Compatibility.BarcodeReader
        {
            AutoRotate = true,
            Options = new DecodingOptions
            {
                PossibleFormats = [BarcodeFormat.QR_CODE],
                TryHarder = true,
                TryInverted = true
            }
        };
        var decoded = reader.DecodeMultiple(bitmap) ?? [];
        return decoded
            .OrderBy(result => Top(result))
            .ThenBy(result => Left(result))
            .Select(result => result.Text)
            .Where(text => text is not null)
            .Distinct(StringComparer.Ordinal)
            .ToArray();
    }

    public static void ValidateDimensions(int width, int height)
    {
        if (width <= 0 || height <= 0 || (long)width * height > MaxPixels)
            throw new IOException("INVALID_IMAGE_DIMENSIONS");
    }

    public static bool IsLocalPath(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || path.Contains('\0')) return false;
        var fullPath = Path.GetFullPath(path);
        if (fullPath.StartsWith(@"\\", StringComparison.Ordinal)) return false;
        // Colons after the drive prefix denote alternate data streams, not image files.
        if (fullPath.AsSpan(2).Contains(':')) return false;
        var root = Path.GetPathRoot(fullPath);
        return root is not null && new DriveInfo(root).DriveType != DriveType.Network;
    }

    private static float Top(Result result) => result.ResultPoints is { Length: > 0 } points
        ? points.Min(point => point.Y) : float.MaxValue;
    private static float Left(Result result) => result.ResultPoints is { Length: > 0 } points
        ? points.Min(point => point.X) : float.MaxValue;
}
