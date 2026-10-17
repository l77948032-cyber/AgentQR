using System.Drawing;
using System.Drawing.Imaging;
using System.Text.Json;
using Xunit;
using ZXing;
using ZXing.QrCode;

namespace ScanBridge.Tests;

public sealed class ScannerTests : IDisposable
{
    private const string Payload = "https://example.com/invoice?token=synthetic-only";
    private readonly string directory = Path.Combine(Path.GetTempPath(), "ScanBridge tests-" + Guid.NewGuid());

    public ScannerTests() => Directory.CreateDirectory(directory);
    public void Dispose() => Directory.Delete(directory, recursive: true);

    [Theory]
    [InlineData(ImageFormatKind.Png)]
    [InlineData(ImageFormatKind.Jpeg)]
    [InlineData(ImageFormatKind.Bmp)]
    public void DecodesRealImageBytes(ImageFormatKind format)
    {
        using var bitmap = Code(Payload);
        var path = Save(bitmap, format);
        Assert.Equal([Payload], new QrScanner().Decode(path));
        var fixtureDirectory = Environment.GetEnvironmentVariable("SCANBRIDGE_FIXTURES_DIR");
        if (format == ImageFormatKind.Png && fixtureDirectory is not null)
        {
            Directory.CreateDirectory(fixtureDirectory);
            File.Copy(path, Path.Combine(fixtureDirectory, "synthetic qr.png"), overwrite: true);
        }
    }

    [Fact]
    public void DecodesRotatedQr()
    {
        using var bitmap = Code(Payload);
        bitmap.RotateFlip(RotateFlipType.Rotate90FlipNone);
        Assert.Equal([Payload], new QrScanner().Decode(Save(bitmap)));
    }

    [Fact]
    public void DecodesUnicodeQr()
    {
        const string text = "\u53d1\u7968\u5185\u5bb9";
        using var bitmap = Code(text);
        Assert.Equal([text], new QrScanner().Decode(Save(bitmap)));
    }

    [Fact]
    public void SortsMultipleCodesAndDeduplicates()
    {
        using var first = Code("https://example.com/first");
        using var second = Code("https://example.com/second");
        using var canvas = new Bitmap(900, 900);
        using (var graphics = Graphics.FromImage(canvas))
        {
            graphics.Clear(Color.White);
            graphics.DrawImageUnscaled(first, 20, 20);
            graphics.DrawImageUnscaled(second, 500, 500);
            graphics.DrawImageUnscaled(first, 20, 500);
        }
        Assert.Equal(["https://example.com/first", "https://example.com/second"],
            new QrScanner().Decode(Save(canvas)));
    }

    [Fact]
    public void BlankImageReturnsEmpty()
    {
        using var bitmap = new Bitmap(400, 400);
        using (var graphics = Graphics.FromImage(bitmap)) graphics.Clear(Color.White);
        Assert.Empty(new QrScanner().Decode(Save(bitmap)));
    }

    [Fact]
    public void InvalidMissingAndOversizedFilesFail()
    {
        var path = Path.Combine(directory, "invalid.png");
        File.WriteAllText(path, "not an image");
        Assert.ThrowsAny<Exception>(() => new QrScanner().Decode(path));
        Assert.ThrowsAny<Exception>(() => new QrScanner().Decode(path + ".missing"));
        using (var file = File.Create(path)) file.SetLength(QrScanner.MaxFileBytes + 1);
        Assert.Throws<IOException>(() => new QrScanner().Decode(path));
    }

    [Fact]
    public void RejectsLargeDimensionsWithoutIntegerOverflow()
    {
        Assert.Throws<IOException>(() => QrScanner.ValidateDimensions(100_000, 100_000));
        Assert.Throws<IOException>(() => QrScanner.ValidateDimensions(0, 100));
        QrScanner.ValidateDimensions(4000, 4000);
    }

    [Theory]
    [InlineData(@"\\example.invalid\share\qr.png")]
    [InlineData(@"\\?\C:\qr.png")]
    [InlineData("https://example.com/qr.png")]
    [InlineData("C:\\qr.png:secret")]
    public void DoesNotReadNetworkOrDevicePaths(string path)
    {
        Assert.ThrowsAny<Exception>(() => new QrScanner().Decode(path));
    }

    [Theory]
    [InlineData("javascript:alert(1)")]
    [InlineData("file:///C:/private.txt")]
    [InlineData("alipays://platformapi/startapp")]
    [InlineData("https://user:secret@example.com")]
    [InlineData("https://example.com\n")]
    [InlineData("https://example.com/\u001b")]
    [InlineData("https://")]
    [InlineData("invoice text")]
    public void RejectsUnsafeOrNonWebPayloads(string payload) => Assert.Null(PayloadPolicy.WebUri(payload));

    [Fact]
    public void RedactionAndClassificationMatchMacShape()
    {
        var result = PayloadPolicy.Describe(Payload, 1);
        Assert.Equal("web", result.Kind);
        Assert.Equal("https://example.com/[redacted]", result.Destination);
        Assert.Null(result.Payload);
        Assert.Equal(Payload, PayloadPolicy.Describe(Payload, 1, reveal: true).Payload);
        Assert.Equal("https://example.com:8443/[redacted]",
            PayloadPolicy.Describe("https://example.com:8443/token", 1).Destination);
        Assert.Equal("text", PayloadPolicy.Describe("invoice data", 1).Kind);
        Assert.Equal("unsupported-scheme", PayloadPolicy.Describe("alipays://start", 1).Kind);
    }

    [Fact]
    public void CommandOutputsJsonWithoutSecretsByDefault()
    {
        using var output = new StringWriter();
        Assert.Equal(0, ScanCommand.Run(["scan", "image.png"], output, _ => [Payload], true));
        using var json = JsonDocument.Parse(output.ToString());
        Assert.Equal(1, json.RootElement[0].GetProperty("index").GetInt32());
        Assert.Equal("web", json.RootElement[0].GetProperty("kind").GetString());
        Assert.False(json.RootElement[0].TryGetProperty("payload", out _));
        Assert.DoesNotContain("synthetic-only", output.ToString());
    }

    [Fact]
    public void CommandRevealReturnsExactUnicodePayload()
    {
        const string payload = "\u53d1\u7968\u5185\u5bb9";
        using var output = new StringWriter();
        Assert.Equal(0, ScanCommand.Run(["scan", "image.png", "--reveal"], output, _ => [payload], true));
        using var json = JsonDocument.Parse(output.ToString());
        Assert.Equal(payload, json.RootElement[0].GetProperty("payload").GetString());
    }

    [Fact]
    public void CommandErrorsAreNoninteractiveAndRedacted()
    {
        CheckError([], _ => throw new Exception(), true, 2, "INVALID_ARGUMENTS");
        CheckError(["scan", "a", "--unknown"], _ => throw new Exception(), true, 2, "INVALID_ARGUMENTS");
        CheckError(["scan", "a"], _ => [], true, 3, "NO_QR_CODE");
        CheckError(["scan", "a"], _ => throw new IOException("private-token"), true, 1, "SCAN_OR_OPEN_FAILED");
        CheckError(["scan", "a"], _ => throw new Exception(), false, 4, "UNSUPPORTED_PLATFORM");
        CheckError(["open", "a"], _ => throw new Exception(), true, 2, "INVALID_ARGUMENTS");
    }

    private static void CheckError(string[] args, Func<string, IReadOnlyList<string>> decode,
        bool windows, int expectedExit, string expectedCode)
    {
        using var output = new StringWriter();
        Assert.Equal(expectedExit, ScanCommand.Run(args, output, decode, windows));
        using var json = JsonDocument.Parse(output.ToString());
        Assert.Equal(expectedCode, json.RootElement.GetProperty("error").GetString());
        Assert.DoesNotContain("private-token", output.ToString());
    }

    internal static Bitmap Code(string payload) => new ZXing.Windows.Compatibility.BarcodeWriter
    {
        Format = BarcodeFormat.QR_CODE,
        Options = new QrCodeEncodingOptions { Width = 320, Height = 320, Margin = 4, CharacterSet = "UTF-8" }
    }.Write(payload);

    private string Save(Bitmap image, ImageFormatKind format = ImageFormatKind.Png)
    {
        var path = Path.Combine(directory, Guid.NewGuid() + ".image");
        image.Save(path, format switch
        {
            ImageFormatKind.Jpeg => ImageFormat.Jpeg,
            ImageFormatKind.Bmp => ImageFormat.Bmp,
            _ => ImageFormat.Png
        });
        return path;
    }

    public enum ImageFormatKind { Png, Jpeg, Bmp }
}
