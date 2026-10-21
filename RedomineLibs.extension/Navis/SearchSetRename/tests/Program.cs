using System;
using System.Collections.Generic;
using System.Reflection;

namespace Redomine.NavisSearchSetRename.Tests
{
    /// <summary>
    /// Минимальный раннер тестов без зависимостей от NuGet/xunit.
    /// Ищет в своей сборке все типы с суффиксом "Tests" и в них публичные методы,
    /// отмеченные [Test]. Запускает их последовательно, агрегирует результаты,
    /// печатает отчёт и возвращает ненулевой код, если что-то упало.
    /// </summary>
    internal static class Program
    {
        [AttributeUsage(AttributeTargets.Method)]
        public sealed class TestAttribute : Attribute
        {
        }

        private static int Main(string[] args)
        {
            int passed = 0;
            int failed = 0;
            var failures = new List<string>();

            Assembly assembly = Assembly.GetExecutingAssembly();
            foreach (Type type in GetTestTypes(assembly))
            {
                object instance;
                try
                {
                    instance = Activator.CreateInstance(type);
                }
                catch (Exception exception)
                {
                    failed++;
                    failures.Add(type.FullName + " :: конструктор: " + exception.Message);
                    continue;
                }

                foreach (MethodInfo method in GetTestMethods(type))
                {
                    string name = type.FullName + "." + method.Name;
                    try
                    {
                        method.Invoke(instance, null);
                        passed++;
                        Console.WriteLine("  PASS  " + name);
                    }
                    catch (TargetInvocationException tie)
                    {
                        failed++;
                        string detail = tie.InnerException != null ? tie.InnerException.Message : tie.Message;
                        failures.Add(name + " :: " + detail);
                        Console.WriteLine("  FAIL  " + name + " :: " + detail);
                    }
                    catch (Exception exception)
                    {
                        failed++;
                        failures.Add(name + " :: " + exception.Message);
                        Console.WriteLine("  FAIL  " + name + " :: " + exception.Message);
                    }
                }
            }

            Console.WriteLine();
            Console.WriteLine("Итого: " + passed + " прошло, " + failed + " упало.");
            if (failed > 0)
            {
                Console.WriteLine();
                Console.WriteLine("Провалы:");
                foreach (string failure in failures)
                {
                    Console.WriteLine("  - " + failure);
                }
                return 1;
            }
            return 0;
        }

        private static IEnumerable<Type> GetTestTypes(Assembly assembly)
        {
            foreach (Type type in assembly.GetTypes())
            {
                if (!type.IsClass || type.IsAbstract)
                {
                    continue;
                }
                if (type.Name.EndsWith("Tests", StringComparison.Ordinal))
                {
                    yield return type;
                }
            }
        }

        private static IEnumerable<MethodInfo> GetTestMethods(Type type)
        {
            foreach (MethodInfo method in type.GetMethods(BindingFlags.Public | BindingFlags.Instance))
            {
                if (method.GetCustomAttribute<TestAttribute>(inherit: false) != null)
                {
                    yield return method;
                }
            }
        }
    }
}
