# gipDebug

This repository contains the GDB binary files required for debugging GlistEngine applications on Windows.

## Setup

### 1. Add GDB to Your Project's PATH

Windows developers must add the following directory to their GlistApp project's PATH:
```
;${workspace_loc}\..\..\..\..\glistplugins\gipDebug\libs\bin
```

**To add this:**
1. Right-click your project -> **Properties**
2. Navigate to **C/C++ Build -> Environment**
3. From the Configuration dropdown list, select Debug.
4. Double click to PATH and add the path above to the end of your previous PATH variable. 

### 2. Create a Debug Run Configuration

1. From the menu bar, click **Run -> Run Configurations...**
2. Select your existing Release configuration (e.g., "GlistApp Release")
3. Click the **Duplicate** button (two stacked documents icon) in the top-right
4. Rename the new configuration to "[ProjectName] Debug"
5. Update the **C/C++ Application** path:
   - Change from: `_build/Release/ProjectName.exe`
   - Change to: `_build/Debug/ProjectName.exe`
6. Set **Build Configuration** to **Debug**
7. Click **Apply**, then **Close**

### 3. Run in Debug Mode

1. Locate the toolbar dropdown menu next to the Build/Run/Stop buttons (top left of your screen)
2. Select **Debug** from the dropdown (the Run icon will change to a bug icon)
3. In the Run Configuration dropdown, select your newly created Debug configuration (e.g., "GlistApp Debug")
4. Click the **bug icon** to launch your application in debug mode

## Troubleshooting

### Debugger Shows Assembly Instead of Source Code

If the debugger displays assembly view instead of your source code, you need to ensure debug symbols are generated during compilation.

**Solution:**

Open your project's `CMakeLists.txt` file and add the following line immediately after the `project()` declaration:
```cmake
set(CMAKE_BUILD_TYPE Debug)
```

**Example:**
```cmake
project(YourProjectName)
set(CMAKE_BUILD_TYPE Debug)
```

After adding this line, clean and rebuild your project to generate the debug symbols. 