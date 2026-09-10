// Installed by unitybuild project init. Keep this file in the Unity project.
#if UNITY_EDITOR
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;

namespace UnityBuild
{
    public static class Builder
    {
        public static void Build()
        {
            var output = Environment.GetEnvironmentVariable("UNITYBUILD_OUTPUT");
            if (string.IsNullOrWhiteSpace(output))
                throw new BuildFailedException("UNITYBUILD_OUTPUT is required.");
            var scenes = EditorBuildSettings.scenes.Where(scene => scene.enabled)
                .Select(scene => scene.path).ToArray();
            if (scenes.Length == 0)
                throw new BuildFailedException("Enable at least one scene in Unity Build Settings.");
            BuildTarget target;
            if (!Enum.TryParse(Environment.GetEnvironmentVariable("UNITYBUILD_PLATFORM"), out target))
                throw new BuildFailedException("Invalid UNITYBUILD_PLATFORM.");
            Directory.CreateDirectory(Path.GetDirectoryName(output));
            var options = BuildOptions.None;
            if (Environment.GetEnvironmentVariable("UNITYBUILD_PROFILE") == "debug")
                options |= BuildOptions.Development | BuildOptions.AllowDebugging;
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = scenes,
                locationPathName = output,
                target = target,
                options = options
            });
            if (report.summary.result != BuildResult.Succeeded)
                throw new BuildFailedException("Build failed: " + report.summary.result);
        }
    }
}
#endif
